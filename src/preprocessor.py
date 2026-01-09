"""
C Preprocessor for uc80 compiler.

Implements full C preprocessor functionality:
- #include "file" and #include <file>
- #define for object-like and function-like macros
- #undef
- #ifdef, #ifndef, #if, #elif, #else, #endif
- #error, #warning, #pragma, #line
- Token pasting (##) and stringification (#)
- Predefined macros (__FILE__, __LINE__, __DATE__, __TIME__, etc.)
- Variadic macros (__VA_ARGS__)
"""

import re
import os
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Macro:
    """Represents a preprocessor macro."""
    name: str
    params: Optional[list[str]] = None  # None for object-like, list for function-like
    body: str = ""
    is_variadic: bool = False
    is_predefined: bool = False


@dataclass
class PreprocessorState:
    """State for conditional compilation."""
    condition_stack: list[bool] = field(default_factory=list)  # True if currently active
    seen_else: list[bool] = field(default_factory=list)  # Track if #else was seen


class PreprocessorError(Exception):
    """Preprocessor error with location info."""
    def __init__(self, message: str, filename: str, line: int):
        self.message = message
        self.filename = filename
        self.line = line
        super().__init__(f"{filename}:{line}: {message}")


class Preprocessor:
    """Full C preprocessor implementation."""

    def __init__(self, include_paths: Optional[list[str]] = None):
        self.include_paths = include_paths or []
        self.macros: dict[str, Macro] = {}
        self.state = PreprocessorState()
        self.current_file = "<unknown>"
        self.current_line = 0
        self.included_files: set[str] = set()  # For include guard tracking
        self.expanding: set[str] = set()  # Prevent recursive macro expansion

        # Initialize predefined macros
        self._init_predefined_macros()

    def _init_predefined_macros(self) -> None:
        """Initialize standard predefined macros."""
        now = datetime.now()

        # Standard C macros
        self.macros["__STDC__"] = Macro("__STDC__", body="1", is_predefined=True)
        self.macros["__STDC_VERSION__"] = Macro("__STDC_VERSION__", body="202311L", is_predefined=True)

        # Date and time (fixed at preprocessing time)
        date_str = now.strftime("%b %d %Y")
        time_str = now.strftime("%H:%M:%S")
        self.macros["__DATE__"] = Macro("__DATE__", body=f'"{date_str}"', is_predefined=True)
        self.macros["__TIME__"] = Macro("__TIME__", body=f'"{time_str}"', is_predefined=True)

        # Compiler identification
        self.macros["__UC80__"] = Macro("__UC80__", body="1", is_predefined=True)
        self.macros["__UC80_VERSION__"] = Macro("__UC80_VERSION__", body="100", is_predefined=True)
        self.macros["__Z80__"] = Macro("__Z80__", body="1", is_predefined=True)
        self.macros["__CPM__"] = Macro("__CPM__", body="1", is_predefined=True)

        # C23/C24 compliance
        self.macros["__STDC_HOSTED__"] = Macro("__STDC_HOSTED__", body="1", is_predefined=True)

        # Useful for version strings - timestamp as integer YYYYMMDD
        date_int = now.strftime("%Y%m%d")
        self.macros["__DATE_INT__"] = Macro("__DATE_INT__", body=date_int, is_predefined=True)

        # Time as integer HHMMSS
        time_int = now.strftime("%H%M%S")
        self.macros["__TIME_INT__"] = Macro("__TIME_INT__", body=time_int, is_predefined=True)

    def preprocess(self, source: str, filename: str = "<stdin>") -> str:
        """Preprocess source code and return the result."""
        self.current_file = filename
        self.current_line = 0

        # Set __FILE__ for this file
        self.macros["__FILE__"] = Macro("__FILE__", body=f'"{filename}"', is_predefined=True)

        lines = source.split('\n')
        output_lines = []

        i = 0
        while i < len(lines):
            self.current_line = i + 1
            # Update __LINE__
            self.macros["__LINE__"] = Macro("__LINE__", body=str(self.current_line), is_predefined=True)

            line = lines[i]

            # Handle line continuation
            while line.endswith('\\') and i + 1 < len(lines):
                line = line[:-1] + lines[i + 1]
                i += 1

            # Check if this is a preprocessor directive
            stripped = line.lstrip()
            if stripped.startswith('#'):
                result = self._process_directive(stripped[1:].strip())
                if result is not None:
                    output_lines.append(result)
            elif self._is_active():
                # Regular line - expand macros
                expanded = self._expand_macros(line)
                output_lines.append(expanded)
            # else: skip line (inactive conditional block)

            i += 1

        # Check for unclosed conditionals
        if self.state.condition_stack:
            raise PreprocessorError("Unterminated #if/#ifdef/#ifndef",
                                   self.current_file, self.current_line)

        return '\n'.join(output_lines)

    def _preprocess_included(self, source: str, filename: str, parent_stack_depth: int) -> str:
        """Preprocess an included file, checking only for conditionals opened in this file."""
        self.current_file = filename
        self.current_line = 0

        # Set __FILE__ for this file
        self.macros["__FILE__"] = Macro("__FILE__", body=f'"{filename}"', is_predefined=True)

        lines = source.split('\n')
        output_lines = []

        i = 0
        while i < len(lines):
            self.current_line = i + 1
            self.macros["__LINE__"] = Macro("__LINE__", body=str(self.current_line), is_predefined=True)

            line = lines[i]

            # Handle line continuation
            while line.endswith('\\') and i + 1 < len(lines):
                line = line[:-1] + lines[i + 1]
                i += 1

            # Check if this is a preprocessor directive
            stripped = line.lstrip()
            if stripped.startswith('#'):
                result = self._process_directive(stripped[1:].strip())
                if result is not None:
                    output_lines.append(result)
            elif self._is_active():
                expanded = self._expand_macros(line)
                output_lines.append(expanded)

            i += 1

        # Check for unclosed conditionals opened in THIS file only
        if len(self.state.condition_stack) > parent_stack_depth:
            raise PreprocessorError("Unterminated #if/#ifdef/#ifndef",
                                   self.current_file, self.current_line)

        return '\n'.join(output_lines)

    def preprocess_file(self, filepath: str) -> str:
        """Preprocess a file."""
        filepath = os.path.abspath(filepath)
        with open(filepath, 'r') as f:
            source = f.read()

        # Add file's directory to include paths
        file_dir = os.path.dirname(filepath)
        if file_dir and file_dir not in self.include_paths:
            self.include_paths.insert(0, file_dir)

        return self.preprocess(source, filepath)

    def _is_active(self) -> bool:
        """Check if current code should be processed (not in inactive conditional)."""
        return all(self.state.condition_stack) if self.state.condition_stack else True

    def _process_directive(self, directive: str) -> Optional[str]:
        """Process a preprocessor directive. Returns output or None."""
        if not directive:
            return None  # Null directive

        # Parse directive name
        match = re.match(r'(\w+)\s*(.*)', directive)
        if not match:
            return None

        name = match.group(1)
        args = match.group(2)

        # Conditional directives are always processed (to track nesting)
        if name in ('if', 'ifdef', 'ifndef', 'elif', 'else', 'endif'):
            return self._process_conditional(name, args)

        # Other directives only processed if active
        if not self._is_active():
            return None

        if name == 'include':
            return self._process_include(args)
        elif name == 'define':
            return self._process_define(args)
        elif name == 'undef':
            return self._process_undef(args)
        elif name == 'error':
            raise PreprocessorError(f"#error {args}", self.current_file, self.current_line)
        elif name == 'warning':
            import sys
            print(f"{self.current_file}:{self.current_line}: warning: {args}", file=sys.stderr)
            return None
        elif name == 'pragma':
            return self._process_pragma(args)
        elif name == 'line':
            return self._process_line(args)
        else:
            raise PreprocessorError(f"Unknown directive: #{name}",
                                   self.current_file, self.current_line)

    def _process_include(self, args: str) -> str:
        """Process #include directive."""
        args = args.strip()

        # Determine include type and filename
        if args.startswith('"'):
            # Find closing quote - handle trailing comments
            end = args.find('"', 1)
            if end == -1:
                raise PreprocessorError(f"Invalid #include syntax: {args}",
                                       self.current_file, self.current_line)
            filename = args[1:end]
            search_paths = [os.path.dirname(self.current_file)] + self.include_paths
        elif args.startswith('<'):
            # Find closing angle bracket - handle trailing comments
            end = args.find('>')
            if end == -1:
                raise PreprocessorError(f"Invalid #include syntax: {args}",
                                       self.current_file, self.current_line)
            filename = args[1:end]
            search_paths = self.include_paths
        else:
            raise PreprocessorError(f"Invalid #include syntax: {args}",
                                   self.current_file, self.current_line)

        # Search for file
        for path in search_paths:
            full_path = os.path.join(path, filename) if path else filename
            if os.path.exists(full_path):
                full_path = os.path.abspath(full_path)

                # Save current state
                saved_file = self.current_file
                saved_line = self.current_line
                saved_stack_depth = len(self.state.condition_stack)

                # Preprocess included file
                with open(full_path, 'r') as f:
                    content = f.read()

                result = self._preprocess_included(content, full_path, saved_stack_depth)

                # Restore state
                self.current_file = saved_file
                self.current_line = saved_line
                self.macros["__FILE__"] = Macro("__FILE__", body=f'"{saved_file}"', is_predefined=True)

                return result

        raise PreprocessorError(f"Cannot find include file: {filename}",
                               self.current_file, self.current_line)

    def _process_define(self, args: str) -> None:
        """Process #define directive."""
        args = args.strip()
        if not args:
            raise PreprocessorError("Expected macro name after #define",
                                   self.current_file, self.current_line)

        # Check for function-like macro: NAME(params)
        match = re.match(r'(\w+)\s*\(\s*([^)]*)\s*\)\s*(.*)', args)
        if match:
            name = match.group(1)
            params_str = match.group(2).strip()
            body = match.group(3).strip()

            # Parse parameters
            is_variadic = False
            if params_str:
                params = [p.strip() for p in params_str.split(',')]
                # Check for variadic
                if params and params[-1] == '...':
                    params[-1] = '__VA_ARGS__'
                    is_variadic = True
                elif params and params[-1].endswith('...'):
                    # Named variadic: name...
                    params[-1] = params[-1][:-3].strip()
                    is_variadic = True
            else:
                params = []

            self.macros[name] = Macro(name, params=params, body=body, is_variadic=is_variadic)
        else:
            # Object-like macro: NAME or NAME value
            match = re.match(r'(\w+)\s*(.*)', args)
            if match:
                name = match.group(1)
                body = match.group(2).strip()
                self.macros[name] = Macro(name, body=body)
            else:
                raise PreprocessorError(f"Invalid #define syntax: {args}",
                                       self.current_file, self.current_line)

        return None

    def _process_undef(self, args: str) -> None:
        """Process #undef directive."""
        name = args.strip()
        if not name or not re.match(r'^\w+$', name):
            raise PreprocessorError(f"Invalid macro name: {name}",
                                   self.current_file, self.current_line)
        if name in self.macros and not self.macros[name].is_predefined:
            del self.macros[name]
        return None

    def _process_conditional(self, directive: str, args: str) -> None:
        """Process conditional compilation directives."""
        if directive == 'ifdef':
            name = args.strip()
            if self._is_active():
                result = name in self.macros
                self.state.condition_stack.append(result)
            else:
                self.state.condition_stack.append(False)
            self.state.seen_else.append(False)

        elif directive == 'ifndef':
            name = args.strip()
            if self._is_active():
                result = name not in self.macros
                self.state.condition_stack.append(result)
            else:
                self.state.condition_stack.append(False)
            self.state.seen_else.append(False)

        elif directive == 'if':
            if self._is_active():
                result = self._evaluate_condition(args)
                self.state.condition_stack.append(result)
            else:
                self.state.condition_stack.append(False)
            self.state.seen_else.append(False)

        elif directive == 'elif':
            if not self.state.condition_stack:
                raise PreprocessorError("#elif without #if",
                                       self.current_file, self.current_line)
            if self.state.seen_else[-1]:
                raise PreprocessorError("#elif after #else",
                                       self.current_file, self.current_line)

            # Check if any previous branch was taken
            prev_taken = self.state.condition_stack[-1]
            self.state.condition_stack.pop()

            # Evaluate this branch only if no previous branch was taken
            if not prev_taken and (len(self.state.condition_stack) == 0 or all(self.state.condition_stack)):
                result = self._evaluate_condition(args)
                self.state.condition_stack.append(result)
            else:
                self.state.condition_stack.append(False)

        elif directive == 'else':
            if not self.state.condition_stack:
                raise PreprocessorError("#else without #if",
                                       self.current_file, self.current_line)
            if self.state.seen_else[-1]:
                raise PreprocessorError("Multiple #else in conditional",
                                       self.current_file, self.current_line)

            # Flip the condition (only if outer conditions are active)
            prev_taken = self.state.condition_stack[-1]
            self.state.condition_stack.pop()

            if len(self.state.condition_stack) == 0 or all(self.state.condition_stack):
                self.state.condition_stack.append(not prev_taken)
            else:
                self.state.condition_stack.append(False)

            self.state.seen_else[-1] = True

        elif directive == 'endif':
            if not self.state.condition_stack:
                raise PreprocessorError("#endif without #if",
                                       self.current_file, self.current_line)
            self.state.condition_stack.pop()
            self.state.seen_else.pop()

        return None

    def _evaluate_condition(self, expr: str) -> bool:
        """Evaluate a preprocessor condition expression."""
        expr = expr.strip()
        if not expr:
            raise PreprocessorError("Empty condition in #if",
                                   self.current_file, self.current_line)

        # Handle defined() and defined NAME
        expr = self._expand_defined(expr)

        # Expand macros in the expression
        expr = self._expand_macros(expr)

        # Replace remaining identifiers with 0 (undefined macros)
        expr = re.sub(r'\b[a-zA-Z_]\w*\b', '0', expr)

        # Evaluate the expression
        try:
            # Use Python's eval with restricted globals
            # Support C-style operators
            expr = expr.replace('&&', ' and ')
            expr = expr.replace('||', ' or ')
            expr = expr.replace('!', ' not ')
            # Handle C-style not-equal that wasn't replaced
            expr = expr.replace(' not =', '!=')

            result = eval(expr, {"__builtins__": {}}, {})
            return bool(result)
        except Exception as e:
            raise PreprocessorError(f"Invalid condition expression: {expr} ({e})",
                                   self.current_file, self.current_line)

    def _expand_defined(self, expr: str) -> str:
        """Expand defined() operator in expression."""
        # defined(NAME)
        expr = re.sub(
            r'\bdefined\s*\(\s*(\w+)\s*\)',
            lambda m: '1' if m.group(1) in self.macros else '0',
            expr
        )
        # defined NAME
        expr = re.sub(
            r'\bdefined\s+(\w+)',
            lambda m: '1' if m.group(1) in self.macros else '0',
            expr
        )
        return expr

    def _expand_macros(self, text: str) -> str:
        """Expand all macros in text."""
        # Keep expanding until no more changes
        max_iterations = 100
        for _ in range(max_iterations):
            new_text = self._expand_macros_once(text)
            if new_text == text:
                break
            text = new_text
        return text

    def _expand_macros_once(self, text: str) -> str:
        """Single pass of macro expansion."""
        result = []
        i = 0

        while i < len(text):
            # Skip strings and character literals
            if text[i] in '"\'':
                quote = text[i]
                j = i + 1
                while j < len(text):
                    if text[j] == '\\' and j + 1 < len(text):
                        j += 2
                    elif text[j] == quote:
                        j += 1
                        break
                    else:
                        j += 1
                result.append(text[i:j])
                i = j
                continue

            # Look for identifier
            match = re.match(r'[a-zA-Z_]\w*', text[i:])
            if match:
                name = match.group()
                end = i + len(name)

                if name in self.macros and name not in self.expanding:
                    macro = self.macros[name]

                    if macro.params is not None:
                        # Function-like macro - need arguments
                        args_match = self._parse_macro_args(text, end)
                        if args_match:
                            args, new_end = args_match
                            expanded = self._expand_function_macro(macro, args)
                            result.append(expanded)
                            i = new_end
                            continue
                        # No arguments - don't expand
                    else:
                        # Object-like macro
                        self.expanding.add(name)
                        expanded = self._expand_macros(macro.body)
                        self.expanding.discard(name)
                        result.append(expanded)
                        i = end
                        continue

                result.append(name)
                i = end
            else:
                result.append(text[i])
                i += 1

        return ''.join(result)

    def _parse_macro_args(self, text: str, start: int) -> Optional[tuple[list[str], int]]:
        """Parse macro arguments starting at position. Returns (args, end_pos) or None."""
        # Skip whitespace
        i = start
        while i < len(text) and text[i] in ' \t':
            i += 1

        if i >= len(text) or text[i] != '(':
            return None

        i += 1  # Skip '('
        args = []
        current_arg = []
        paren_depth = 1

        while i < len(text) and paren_depth > 0:
            ch = text[i]

            if ch == '(':
                paren_depth += 1
                current_arg.append(ch)
            elif ch == ')':
                paren_depth -= 1
                if paren_depth > 0:
                    current_arg.append(ch)
            elif ch == ',' and paren_depth == 1:
                args.append(''.join(current_arg).strip())
                current_arg = []
            elif ch in '"\'':
                # Handle string/char literal
                quote = ch
                current_arg.append(ch)
                i += 1
                while i < len(text):
                    if text[i] == '\\' and i + 1 < len(text):
                        current_arg.append(text[i:i+2])
                        i += 2
                        continue
                    current_arg.append(text[i])
                    if text[i] == quote:
                        break
                    i += 1
            else:
                current_arg.append(ch)

            i += 1

        if paren_depth != 0:
            return None

        # Add last argument
        if current_arg or args:
            args.append(''.join(current_arg).strip())

        return (args, i)

    def _expand_function_macro(self, macro: Macro, args: list[str]) -> str:
        """Expand a function-like macro with given arguments."""
        body = macro.body

        # Handle variadic macros
        if macro.is_variadic:
            if len(args) < len(macro.params):
                args.extend([''] * (len(macro.params) - len(args)))
            # __VA_ARGS__ or the variadic parameter gets remaining args
            va_args = ','.join(args[len(macro.params)-1:]) if len(args) >= len(macro.params) else ''
            args = args[:len(macro.params)-1] + [va_args]

        # Pad or truncate args to match params
        while len(args) < len(macro.params):
            args.append('')

        # Handle # (stringification) operator
        body = self._handle_stringification(body, macro.params, args)

        # Handle ## (token pasting) operator
        body = self._handle_token_pasting(body, macro.params, args)

        # Regular parameter substitution
        for param, arg in zip(macro.params, args):
            # Replace parameter with argument (word boundary)
            body = re.sub(rf'\b{re.escape(param)}\b', arg, body)

        # Mark macro as being expanded to prevent recursion
        self.expanding.add(macro.name)
        result = self._expand_macros(body)
        self.expanding.discard(macro.name)

        return result

    def _handle_stringification(self, body: str, params: list[str], args: list[str]) -> str:
        """Handle # operator (stringification)."""
        for param, arg in zip(params, args):
            # Match # followed by parameter, but NOT ## (token pasting)
            # Use negative lookbehind to avoid matching ##
            pattern = rf'(?<!#)#\s*{re.escape(param)}\b'
            # Stringify the argument
            stringified = '"' + arg.replace('\\', '\\\\').replace('"', '\\"') + '"'
            body = re.sub(pattern, stringified, body)
        return body

    def _handle_token_pasting(self, body: str, params: list[str], args: list[str]) -> str:
        """Handle ## operator (token pasting)."""
        # First substitute parameters that are part of ## operations
        for param, arg in zip(params, args):
            # param ## something
            body = re.sub(rf'\b{re.escape(param)}\s*##', arg + '##', body)
            # something ## param
            body = re.sub(rf'##\s*{re.escape(param)}\b', '##' + arg, body)

        # Now remove ## and concatenate tokens
        body = re.sub(r'\s*##\s*', '', body)

        return body

    def _process_pragma(self, args: str) -> None:
        """Process #pragma directive."""
        # For now, just ignore pragmas
        # Could implement: #pragma once, etc.
        return None

    def _process_line(self, args: str) -> None:
        """Process #line directive."""
        match = re.match(r'(\d+)(?:\s+"([^"]*)")?', args.strip())
        if match:
            self.current_line = int(match.group(1)) - 1  # Will be incremented
            if match.group(2):
                self.current_file = match.group(2)
                self.macros["__FILE__"] = Macro("__FILE__", body=f'"{self.current_file}"', is_predefined=True)
        return None


def preprocess(source: str, filename: str = "<stdin>",
               include_paths: Optional[list[str]] = None) -> str:
    """Convenience function to preprocess source code."""
    pp = Preprocessor(include_paths)
    return pp.preprocess(source, filename)


def preprocess_file(filepath: str,
                    include_paths: Optional[list[str]] = None) -> str:
    """Convenience function to preprocess a file."""
    pp = Preprocessor(include_paths)
    return pp.preprocess_file(filepath)
