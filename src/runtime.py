"""Runtime library handling for uc80.

Parses assembly files to extract individual functions, allowing the compiler
to embed only the runtime functions that are actually used.
"""

import re
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class AsmFunction:
    """An assembly function extracted from a .mac file."""
    name: str
    source: str
    dependencies: set[str] = field(default_factory=set)  # Other functions this calls
    publics: set[str] = field(default_factory=set)  # PUBLIC labels in this function
    externs: set[str] = field(default_factory=set)  # EXTRN dependencies


class RuntimeLibrary:
    """Manages runtime library functions.

    Parses .mac files to extract individual functions and their dependencies,
    allowing selective inclusion of only the functions actually used.
    """

    def __init__(self):
        self.functions: dict[str, AsmFunction] = {}
        self.data_sections: list[str] = []  # DSEG content

    def load_file(self, path: Path) -> None:
        """Load and parse a .mac assembly file."""
        content = path.read_text()
        self._parse_assembly(content)

    def _parse_assembly(self, content: str) -> None:
        """Parse assembly content into individual functions.

        Each function is identified by a PUBLIC declaration followed by its label.
        A new function begins when we see a label that was declared PUBLIC.
        """
        lines = content.splitlines()

        current_func: str | None = None
        current_lines: list[str] = []
        current_publics: set[str] = set()
        current_externs: set[str] = set()
        in_dseg = False
        dseg_lines: list[str] = []
        pending_publics: set[str] = set()  # PUBLIC declarations not yet matched to labels

        # First pass: collect all PUBLIC labels
        all_publics: set[str] = set()
        for line in lines:
            match = re.match(r'\s*PUBLIC\s+([^\s,]+(?:\s*,\s*[^\s,]+)*)', line, re.IGNORECASE)
            if match:
                labels = [l.strip() for l in match.group(1).split(',')]
                all_publics.update(labels)

        for line in lines:
            stripped = line.strip()
            upper = stripped.upper()

            # Track segment changes
            if upper == 'DSEG':
                in_dseg = True
                if current_func and current_lines:
                    self._save_function(current_func, current_lines,
                                       current_publics, current_externs, all_publics)
                current_func = None
                current_lines = []
                current_publics = set()
                pending_publics = set()
                continue
            elif upper == 'CSEG':
                in_dseg = False
                continue
            elif upper.startswith('END'):
                continue
            elif upper == '.Z80':
                continue

            if in_dseg:
                dseg_lines.append(line)
                continue

            # Check for PUBLIC declaration
            match = re.match(r'\s*PUBLIC\s+([^\s,]+(?:\s*,\s*[^\s,]+)*)', line, re.IGNORECASE)
            if match:
                labels = [l.strip() for l in match.group(1).split(',')]

                # If any of these labels are different from the current function,
                # this might be the start of a new function section
                new_func_labels = [l for l in labels if l in all_publics and l != current_func]
                if new_func_labels and current_func is not None:
                    # Save current function before starting to collect for new one
                    if current_lines:
                        self._save_function(current_func, current_lines,
                                           current_publics, current_externs, all_publics)
                    current_func = None
                    current_lines = []
                    current_publics = set()
                    current_externs = set()

                # These are pending until we see their labels
                pending_publics.update(labels)
                if current_func is not None:
                    current_lines.append(line)
                continue

            # Check for EXTRN declaration
            match = re.match(r'\s*EXTRN\s+(\w+)', line, re.IGNORECASE)
            if match:
                if current_func is not None:
                    current_externs.add(match.group(1))
                    current_lines.append(line)
                continue

            # Check for label
            match = re.match(r'^(\w+):', line)
            if match:
                label = match.group(1)

                # Is this a PUBLIC label (start of a new function)?
                if label in all_publics:
                    # Save previous function if any
                    if current_func and current_lines:
                        self._save_function(current_func, current_lines,
                                           current_publics, current_externs, all_publics)

                    # Start new function
                    current_func = label
                    current_publics = {label}
                    # Add any other pending publics that belong to this function
                    # (multiple PUBLIC declarations before the label)
                    for pub in list(pending_publics):
                        if pub == label:
                            pending_publics.discard(pub)
                    current_externs = set()
                    current_lines = [line]
                    continue

            # Regular line - add to current function
            if current_func is not None:
                current_lines.append(line)

        # Save last function
        if current_func and current_lines:
            self._save_function(current_func, current_lines,
                               current_publics, current_externs, all_publics)

        # Save DSEG content
        if dseg_lines:
            self.data_sections.extend(dseg_lines)

    def _save_function(self, name: str, lines: list[str],
                      publics: set[str], externs: set[str],
                      all_publics: set[str]) -> None:
        """Save a parsed function."""
        source = '\n'.join(lines)

        # Find dependencies - calls to other PUBLIC functions
        deps: set[str] = set()
        for line in lines:
            # Look for CALL instructions
            match = re.search(r'\bCALL\s+(\w+)', line, re.IGNORECASE)
            if match:
                target = match.group(1)
                if target in all_publics and target not in publics:
                    deps.add(target)
            # Look for JP/JR to other functions (rare but possible)
            match = re.search(r'\b(?:JP|JR)\s+(?:[A-Z]+,\s*)?(\w+)', line, re.IGNORECASE)
            if match:
                target = match.group(1)
                if target in all_publics and target not in publics:
                    deps.add(target)

        func = AsmFunction(
            name=name,
            source=source,
            dependencies=deps,
            publics=publics,
            externs=externs
        )

        # Register under all PUBLIC names
        for pub in publics:
            self.functions[pub] = func

    def get_function(self, name: str) -> AsmFunction | None:
        """Get a function by name."""
        return self.functions.get(name)

    def get_required_functions(self, needed: set[str]) -> list[AsmFunction]:
        """Get all functions required to satisfy the given set of names.

        Follows dependencies transitively to include everything needed.
        Returns functions in dependency order (dependencies first).
        """
        required: set[str] = set()
        seen_funcs: set[int] = set()  # Track by id to handle multi-PUBLIC funcs

        def add_with_deps(name: str) -> None:
            if name in required:
                return
            func = self.functions.get(name)
            if func is None:
                return
            required.add(name)
            for dep in func.dependencies:
                add_with_deps(dep)

        for name in needed:
            add_with_deps(name)

        # Collect unique functions
        result: list[AsmFunction] = []
        for name in required:
            func = self.functions[name]
            if id(func) not in seen_funcs:
                seen_funcs.add(id(func))
                result.append(func)

        # Sort by dependencies (simple topological sort)
        # Functions with fewer dependencies come first
        result.sort(key=lambda f: len(f.dependencies))

        return result

    def get_data_section(self, functions: list[AsmFunction]) -> str:
        """Get DSEG content needed for the given functions.

        Only includes data labels that are referenced by the embedded functions.
        """
        if not functions or not self.data_sections:
            return ''

        # Collect all labels referenced by the embedded functions
        referenced: set[str] = set()
        for func in functions:
            for line in func.source.splitlines():
                # Skip comment lines
                stripped = line.strip()
                if stripped.startswith(';'):
                    continue
                # Remove inline comments
                if ';' in line:
                    line = line[:line.index(';')]
                # Look for references to labels (in LD, CALL, etc.)
                # Match patterns like (__label), label, etc.
                matches = re.findall(r'\b(__\w+)\b', line)
                referenced.update(matches)

        # Parse DSEG into blocks and only include referenced ones
        result_lines: list[str] = []
        current_label: str | None = None
        current_block: list[str] = []
        in_block = False

        for line in self.data_sections:
            stripped = line.strip()

            # Check for label definition
            match = re.match(r'^(\w+):', stripped)
            if match:
                # Save previous block if referenced
                if current_label and current_label in referenced:
                    result_lines.extend(current_block)

                current_label = match.group(1)
                current_block = [line]
                in_block = True
                continue

            # Check for PUBLIC declaration - include if any of its labels are referenced
            match = re.match(r'\s*PUBLIC\s+(.+)', stripped, re.IGNORECASE)
            if match:
                labels = [l.strip() for l in match.group(1).split(',')]
                if any(l in referenced for l in labels):
                    result_lines.append(line)
                continue

            # Regular line in current block
            if in_block:
                current_block.append(line)

        # Save last block if referenced
        if current_label and current_label in referenced:
            result_lines.extend(current_block)

        return '\n'.join(result_lines)


def load_runtime_library() -> RuntimeLibrary:
    """Load the default runtime library."""
    lib = RuntimeLibrary()
    runtime_path = Path(__file__).parent.parent / "lib" / "runtime.mac"
    if runtime_path.exists():
        lib.load_file(runtime_path)
    return lib
