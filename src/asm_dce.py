"""Assembly-level dead code elimination for Z80.

Removes unreachable code from assembly output by tracing reachability
from entry points (main and PUBLIC functions).
"""

import re
from dataclasses import dataclass, field


@dataclass
class AsmBlock:
    """A block of assembly code starting at a label."""
    label: str
    lines: list[str] = field(default_factory=list)
    successors: set[str] = field(default_factory=set)  # Labels this block can jump to
    is_public: bool = False
    is_entry: bool = False  # main or address-taken


class AssemblyDCE:
    """Dead code elimination for Z80 assembly."""

    def __init__(self):
        self.blocks: dict[str, AsmBlock] = {}
        self.public_labels: set[str] = set()
        self.extrn_labels: set[str] = set()
        self.header_lines: list[str] = []
        self.footer_lines: list[str] = []
        self.dseg_lines: list[str] = []
        self.current_segment = "CSEG"

    def eliminate_dead_code(self, asm_text: str, entry_points: set[str] | None = None) -> str:
        """Remove unreachable code from assembly.

        Args:
            asm_text: The assembly source text
            entry_points: Set of entry point labels. If None, uses _main and all PUBLIC.

        Returns:
            Assembly with dead code removed.
        """
        self._parse_assembly(asm_text)

        # Determine entry points
        if entry_points is None:
            entry_points = set()
            if "_main" in self.blocks:
                entry_points.add("_main")
            # All PUBLIC labels are potential entry points
            entry_points.update(self.public_labels)

        # Find reachable blocks
        reachable = self._find_reachable(entry_points)

        # Rebuild assembly with only reachable blocks
        return self._rebuild_assembly(reachable)

    def _parse_assembly(self, asm_text: str) -> None:
        """Parse assembly into blocks."""
        lines = asm_text.splitlines()

        current_block: AsmBlock | None = None
        in_header = True
        in_dseg = False

        for line in lines:
            stripped = line.strip()
            upper = stripped.upper()

            # Track segment changes
            if upper == 'DSEG':
                in_dseg = True
                self.current_segment = "DSEG"
                if current_block:
                    self.blocks[current_block.label] = current_block
                    current_block = None
                continue
            elif upper == 'CSEG':
                in_dseg = False
                self.current_segment = "CSEG"
                if in_header:
                    self.header_lines.append(line)
                continue

            # Collect DSEG content separately
            if in_dseg:
                self.dseg_lines.append(line)
                continue

            # Track PUBLIC declarations
            match = re.match(r'\s*PUBLIC\s+(.+)', line, re.IGNORECASE)
            if match:
                labels = [l.strip() for l in match.group(1).split(',')]
                self.public_labels.update(labels)
                if in_header:
                    self.header_lines.append(line)
                elif current_block:
                    current_block.lines.append(line)
                continue

            # Track EXTRN declarations
            match = re.match(r'\s*EXTRN\s+(.+)', line, re.IGNORECASE)
            if match:
                labels = [l.strip() for l in match.group(1).split(',')]
                self.extrn_labels.update(labels)
                if in_header:
                    self.header_lines.append(line)
                elif current_block:
                    current_block.lines.append(line)
                continue

            # Check for END directive
            if upper == 'END' or upper.startswith('END\t') or upper.startswith('END '):
                if current_block:
                    self.blocks[current_block.label] = current_block
                self.footer_lines.append(line)
                continue

            # Header directives
            if upper in ('.Z80', '') or upper.startswith(';'):
                if in_header and current_block is None:
                    self.header_lines.append(line)
                elif current_block:
                    current_block.lines.append(line)
                continue

            # Check for label
            match = re.match(r'^(\@?\??\w+):', line)
            if match:
                label = match.group(1)
                in_header = False

                # Save previous block
                if current_block:
                    self.blocks[current_block.label] = current_block

                # Start new block
                current_block = AsmBlock(
                    label=label,
                    lines=[line],
                    is_public=label in self.public_labels
                )

                # Check if this is an entry point
                if label == "_main" or label in self.public_labels:
                    current_block.is_entry = True

                continue

            # Regular instruction - add to current block
            if current_block:
                current_block.lines.append(line)

                # Track control flow
                self._analyze_control_flow(stripped, current_block)
            elif in_header:
                self.header_lines.append(line)

        # Save final block
        if current_block:
            self.blocks[current_block.label] = current_block

    def _analyze_control_flow(self, line: str, block: AsmBlock) -> None:
        """Analyze a line for control flow targets."""
        upper = line.upper()

        # CALL instruction
        match = re.match(r'\s*CALL\s+(\@?\??\w+)', line, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        # Unconditional JP
        match = re.match(r'\s*JP\s+(\@?\??\w+)\s*$', line, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        # Conditional JP
        match = re.match(r'\s*JP\s+\w+,\s*(\@?\??\w+)', line, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        # JR instructions
        match = re.match(r'\s*JR\s+(\@?\??\w+)\s*$', line, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        match = re.match(r'\s*JR\s+\w+,\s*(\@?\??\w+)', line, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        # DJNZ
        match = re.match(r'\s*DJNZ\s+(\@?\??\w+)', line, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

    def _find_reachable(self, entry_points: set[str]) -> set[str]:
        """Find all blocks reachable from entry points."""
        reachable: set[str] = set()
        worklist = list(entry_points)

        while worklist:
            label = worklist.pop()
            if label in reachable:
                continue
            if label not in self.blocks:
                continue  # External or undefined

            reachable.add(label)
            block = self.blocks[label]

            # Add all successors
            for succ in block.successors:
                if succ not in reachable:
                    worklist.append(succ)

            # Fall-through to next block (if no unconditional jump/ret at end)
            if not self._block_ends_with_terminator(block):
                # Find next block in source order
                next_label = self._find_next_block(label)
                if next_label and next_label not in reachable:
                    worklist.append(next_label)

        return reachable

    def _block_ends_with_terminator(self, block: AsmBlock) -> bool:
        """Check if block ends with unconditional control transfer."""
        for line in reversed(block.lines):
            stripped = line.strip().upper()
            if not stripped or stripped.startswith(';'):
                continue
            # Unconditional terminators
            if stripped.startswith('RET') and ',' not in stripped:
                return True
            if stripped.startswith('JP ') and ',' not in stripped:
                return True
            if stripped.startswith('JR ') and ',' not in stripped:
                return True
            # Any other instruction - not a terminator
            return False
        return False

    def _find_next_block(self, label: str) -> str | None:
        """Find the block that follows the given label in source order."""
        labels = list(self.blocks.keys())
        try:
            idx = labels.index(label)
            if idx + 1 < len(labels):
                return labels[idx + 1]
        except ValueError:
            pass
        return None

    def _rebuild_assembly(self, reachable: set[str]) -> str:
        """Rebuild assembly with only reachable blocks."""
        lines = []

        # Header (includes CSEG if present)
        lines.extend(self.header_lines)

        # Reachable blocks in original order
        for label, block in self.blocks.items():
            if label in reachable:
                lines.extend(block.lines)

        # DSEG
        if self.dseg_lines:
            lines.append("")
            lines.append("\tDSEG")
            lines.extend(self.dseg_lines)

        # Footer (END)
        if self.footer_lines:
            lines.append("")
            lines.extend(self.footer_lines)

        return '\n'.join(lines)


def eliminate_dead_code(asm_text: str, entry_points: set[str] | None = None) -> str:
    """Remove unreachable code from assembly.

    Args:
        asm_text: The assembly source text
        entry_points: Set of entry point labels. If None, uses _main and all PUBLIC.

    Returns:
        Assembly with dead code removed.
    """
    dce = AssemblyDCE()
    return dce.eliminate_dead_code(asm_text, entry_points)
