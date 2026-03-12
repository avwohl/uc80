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


@dataclass
class DataBlock:
    """A block of data in DSEG starting at a label."""
    label: str
    lines: list[str] = field(default_factory=list)
    is_public: bool = False


class AssemblyDCE:
    """Dead code elimination for Z80 assembly."""

    def __init__(self):
        self.blocks: dict[str, AsmBlock] = {}
        self.data_blocks: dict[str, DataBlock] = {}
        self.public_labels: set[str] = set()
        self.public_data_labels: set[str] = set()
        self.extrn_labels: set[str] = set()
        self.header_lines: list[str] = []
        self.footer_lines: list[str] = []
        self.dseg_lines: list[str] = []
        self.common_lines: list[str] = []  # COMMON segment lines (BSS)
        self.current_segment = "CSEG"
        self._explicit_entry = False  # True when caller provides entry points

    def eliminate_dead_code(self, asm_text: str, entry_points: set[str] | None = None) -> str:
        """Remove unreachable code and data from assembly.

        Args:
            asm_text: The assembly source text
            entry_points: Set of entry point labels. If None, uses _main and all PUBLIC.

        Returns:
            Assembly with dead code and data removed.
        """
        self._parse_assembly(asm_text)

        # Determine entry points
        if entry_points is not None:
            self._explicit_entry = True
        else:
            entry_points = set()
            if "_main" in self.blocks:
                entry_points.add("_main")
            # All PUBLIC labels are potential entry points
            entry_points.update(self.public_labels)

        # Find reachable code blocks
        reachable_code = self._find_reachable(entry_points)

        # Find referenced data blocks
        referenced_data = self._find_referenced_data(reachable_code)

        # Rebuild assembly with only reachable blocks and referenced data
        return self._rebuild_assembly(reachable_code, referenced_data)

    def _parse_assembly(self, asm_text: str) -> None:
        """Parse assembly into blocks."""
        lines = asm_text.splitlines()

        current_block: AsmBlock | None = None
        in_header = True
        in_dseg = False
        in_common = False

        for line in lines:
            stripped = line.strip()
            upper = stripped.upper()

            # Track segment changes
            if upper == 'DSEG':
                in_dseg = True
                in_common = False
                self.current_segment = "DSEG"
                if current_block:
                    self.blocks[current_block.label] = current_block
                    current_block = None
                continue
            elif upper == 'CSEG':
                in_dseg = False
                in_common = False
                self.current_segment = "CSEG"
                if in_header:
                    self.header_lines.append(line)
                continue
            elif upper.startswith('COMMON'):
                in_common = True
                in_dseg = False
                self.current_segment = "COMMON"
                if current_block:
                    self.blocks[current_block.label] = current_block
                    current_block = None
                self.common_lines.append(line)
                continue

            # Collect COMMON content separately (BSS - always preserved)
            if in_common:
                self.common_lines.append(line)
                continue

            # Collect DSEG content separately
            if in_dseg:
                self.dseg_lines.append(line)
                continue

            # Track PUBLIC declarations - always preserve
            match = re.match(r'\s*PUBLIC\s+(.+)', line, re.IGNORECASE)
            if match:
                labels = [l.strip() for l in match.group(1).split(',')]
                self.public_labels.update(labels)
                # Always add PUBLIC to header (they need to be at the top)
                self.header_lines.append(line)
                continue

            # Track EXTRN declarations - always preserve in header
            # (they may appear anywhere in the source but need to be at top of output)
            match = re.match(r'\s*EXTRN\s+(.+)', line, re.IGNORECASE)
            if match:
                labels = [l.strip() for l in match.group(1).split(',')]
                self.extrn_labels.update(labels)
                # Always add to header_lines to preserve them
                self.header_lines.append(line)
                continue

            # Check for END directive
            if upper == 'END' or upper.startswith('END\t') or upper.startswith('END '):
                if current_block:
                    self.blocks[current_block.label] = current_block
                self.footer_lines.append(line)
                continue

            # EQU constants - always preserve in header
            if '\tEQU\t' in upper or ' EQU ' in upper:
                self.header_lines.append(line)
                continue

            # Header directives
            if upper in ('.Z80', '') or upper.startswith(';'):
                if in_header and current_block is None:
                    self.header_lines.append(line)
                elif current_block:
                    current_block.lines.append(line)
                continue

            # Check for label
            match = re.match(r'^(\@?\?*\w+):', line)
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

        # Parse DSEG into data blocks
        self._parse_data_blocks()

    def _parse_data_blocks(self) -> None:
        """Parse DSEG lines into individual data blocks."""
        current_data: DataBlock | None = None
        pending_public: set[str] = set()

        for line in self.dseg_lines:
            stripped = line.strip()

            # Track PUBLIC declarations in DSEG
            match = re.match(r'\s*PUBLIC\s+(.+)', line, re.IGNORECASE)
            if match:
                labels = [l.strip() for l in match.group(1).split(',')]
                pending_public.update(labels)
                self.public_data_labels.update(labels)
                if current_data:
                    current_data.lines.append(line)
                continue

            # Check for label (with optional colon)
            # Patterns: "label:" or "label:\tDS 2" or "label\tDW 0"
            # Note: Labels may have @ or multiple ? prefixes (e.g., ??AUTO)
            match = re.match(r'^(\@?\?*\w+):?\s*(DS|DW|DB)?', line)
            if match and (match.group(2) or line.strip().endswith(':')):
                label = match.group(1)

                # Save previous block
                if current_data:
                    self.data_blocks[current_data.label] = current_data

                # Start new block
                is_pub = label in pending_public or label in self.public_data_labels
                current_data = DataBlock(
                    label=label,
                    lines=[line],
                    is_public=is_pub
                )
                pending_public.discard(label)
                continue

            # Regular line (data, comment, etc.) - add to current block
            if current_data:
                current_data.lines.append(line)

        # Save final block
        if current_data:
            self.data_blocks[current_data.label] = current_data

    def _find_referenced_data(self, reachable_code: set[str]) -> set[str]:
        """Find all data labels referenced by reachable code and other data."""
        referenced: set[str] = set()

        # Collect all lines from reachable code blocks
        for label in reachable_code:
            if label not in self.blocks:
                continue
            block = self.blocks[label]
            for line in block.lines:
                # Skip full comment lines (may start with whitespace)
                stripped = line.strip()
                if stripped.startswith(';'):
                    continue
                # Remove inline comments before checking for references
                code_part = line
                if ';' in code_part:
                    code_part = code_part[:code_part.index(';')]

                # Look for references to data labels
                for data_label in self.data_blocks:
                    # Match label (not part of another label)
                    # Use explicit boundary check since \b doesn't work with ?@ prefixes
                    pattern = rf'(?<![a-zA-Z0-9_?@]){re.escape(data_label)}(?![a-zA-Z0-9_])'
                    if re.search(pattern, code_part):
                        referenced.add(data_label)

        # Preserve PUBLIC data labels only in non-explicit mode
        # (when explicit entry points are given, we're in whole-program mode
        # and data should only survive if actually referenced by live code)
        if not self._explicit_entry:
            for data_label in self.data_blocks:
                if self.data_blocks[data_label].is_public:
                    referenced.add(data_label)

        # Iteratively find data referenced from other data (fixpoint)
        changed = True
        while changed:
            changed = False
            for data_label in list(referenced):
                if data_label not in self.data_blocks:
                    continue
                data_block = self.data_blocks[data_label]
                for line in data_block.lines:
                    # Skip comments
                    stripped = line.strip()
                    if stripped.startswith(';'):
                        continue
                    code_part = line
                    if ';' in code_part:
                        code_part = code_part[:code_part.index(';')]

                    # Check for references to other data labels
                    for other_label in self.data_blocks:
                        if other_label in referenced:
                            continue
                        pattern = rf'(?<![a-zA-Z0-9_?@]){re.escape(other_label)}(?![a-zA-Z0-9_])'
                        if re.search(pattern, code_part):
                            referenced.add(other_label)
                            changed = True

        return referenced

    def _analyze_control_flow(self, line: str, block: AsmBlock) -> None:
        """Analyze a line for control flow targets."""
        # Strip trailing comments before matching control flow
        code = line.split(';')[0].rstrip() if ';' in line else line

        # CALL instruction
        match = re.match(r'\s*CALL\s+(\@?\?*\w+)', code, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        # Unconditional JP
        match = re.match(r'\s*JP\s+(\@?\?*\w+)\s*$', code, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        # Conditional JP
        match = re.match(r'\s*JP\s+\w+,\s*(\@?\?*\w+)', code, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        # JR instructions
        match = re.match(r'\s*JR\s+(\@?\?*\w+)\s*$', code, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        match = re.match(r'\s*JR\s+\w+,\s*(\@?\?*\w+)', code, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        # DJNZ
        match = re.match(r'\s*DJNZ\s+(\@?\?*\w+)', code, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        # DW with label reference (jump tables)
        match = re.match(r'\s*DW\s+(\@?\?*\w+)', code, re.IGNORECASE)
        if match:
            target = match.group(1)
            block.successors.add(target)
            return

        # LD reg,label (address-taken labels, e.g. LD DE,@SWTAB)
        match = re.match(r'\s*LD\s+\w+,\s*(\@\w+)', code, re.IGNORECASE)
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
            stripped = line.strip()
            # Strip trailing comments
            if ';' in stripped:
                stripped = stripped[:stripped.index(';')].rstrip()
            stripped = stripped.upper()
            if not stripped:
                continue
            # Unconditional terminators
            # RET alone is unconditional, RET Z/NZ/C/NC/PE/PO/P/M are conditional
            if stripped == 'RET':
                return True
            # JP (HL) or JP (IX) or JP (IY) are unconditional
            if stripped.startswith('JP\t(') or stripped.startswith('JP ('):
                return True
            # JP label (no condition) is unconditional
            if stripped.startswith('JP\t') or stripped.startswith('JP '):
                rest = stripped[3:].strip()
                # Check if first part is a condition code
                conds = ('Z,', 'NZ,', 'C,', 'NC,', 'PE,', 'PO,', 'P,', 'M,')
                if not any(rest.startswith(c) for c in conds):
                    return True
            # JR label (no condition) is unconditional
            if stripped.startswith('JR\t') or stripped.startswith('JR '):
                rest = stripped[3:].strip()
                conds = ('Z,', 'NZ,', 'C,', 'NC,')
                if not any(rest.startswith(c) for c in conds):
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

    def _rebuild_assembly(self, reachable_code: set[str], referenced_data: set[str]) -> str:
        """Rebuild assembly with only reachable blocks and referenced data."""
        lines = []

        # Compute surviving labels for filtering PUBLIC/EXTRN
        surviving = reachable_code | referenced_data
        # EQU constants are always preserved (they're in header_lines),
        # so their PUBLIC declarations should survive too
        for line in self.header_lines:
            m = re.match(r'^(\@?\?*\w+)\s+EQU\s', line, re.IGNORECASE)
            if m:
                surviving.add(m.group(1))

        # All labels defined by eliminated blocks (for EXTRN cleanup)
        eliminated_defined = set()
        for label in self.blocks:
            if label not in reachable_code:
                eliminated_defined.add(label)
        for label in self.data_blocks:
            if label not in referenced_data:
                eliminated_defined.add(label)

        # Header — filter PUBLIC/EXTRN for eliminated blocks
        for line in self.header_lines:
            match = re.match(r'\s*PUBLIC\s+(.+)', line, re.IGNORECASE)
            if match:
                labels = [l.strip() for l in match.group(1).split(',')]
                kept = [l for l in labels if l in surviving]
                if kept:
                    lines.append(f"\tPUBLIC\t{','.join(kept)}")
                continue
            match = re.match(r'\s*EXTRN\s+(.+)', line, re.IGNORECASE)
            if match:
                labels = [l.strip() for l in match.group(1).split(',')]
                # Remove labels that were defined by now-eliminated blocks
                kept = [l for l in labels if l not in eliminated_defined]
                if kept:
                    lines.append(f"\tEXTRN\t{','.join(kept)}")
                continue
            lines.append(line)

        # Reachable code blocks in original order
        for label, block in self.blocks.items():
            if label in reachable_code:
                lines.extend(block.lines)

        # DSEG - only include referenced data blocks
        if self.data_blocks and referenced_data:
            lines.append("")
            lines.append("\tDSEG")
            for label, data_block in self.data_blocks.items():
                if label in referenced_data:
                    lines.extend(data_block.lines)

        # COMMON segment (BSS) - always preserved
        if self.common_lines:
            lines.append("")
            lines.extend(self.common_lines)

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
