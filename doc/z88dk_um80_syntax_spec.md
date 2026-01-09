# Z88DK to UM80 Assembler Syntax Specification

## Overview

This document specifies the differences between z88dk's z80asm syntax and um80 (MACRO-80 compatible) syntax, and outlines the changes needed to um80 to support z88dk source files directly.

## Current Syntax Comparison

### Number Formats

| Format | z88dk | um80 | Notes |
|--------|-------|------|-------|
| Hex with H suffix | `0FFH`, `0ABCDh` | `0FFH`, `0ABCDH` | **Both support** |
| Hex with $ prefix | `$FF`, `$ABCD` | Not supported | $ is digit separator in um80 |
| Hex with 0x prefix | `0xFF`, `0xABCD` | Not supported | C-style |
| Binary with B suffix | `10101010B` | `10101010B` | **Both support** |
| Binary with % prefix | `%10101010` | Not supported | z88dk style |
| Octal with O/Q suffix | `377O`, `377Q` | `377O`, `377Q` | **Both support** |
| Decimal | `255`, `255D` | `255`, `255D` | **Both support** |
| X'nn' hex | X'FF' | X'FF' | **Both support** |

### Directives

| Directive | z88dk | um80 | Notes |
|-----------|-------|------|-------|
| Section | `SECTION name` | Not supported | Use CSEG/DSEG |
| Public export | `PUBLIC sym` | `PUBLIC sym` | **Both support** |
| External import | `EXTERN sym` | `EXTRN sym` | Different spelling |
| Constant definition | `DEFC sym = val` | `sym EQU val` | Different syntax |
| Define byte | `DEFB`, `DB` | `DB`, `DEFB` | **Both support** |
| Define word | `DEFW`, `DW` | `DW`, `DEFW` | **Both support** |
| Define quad (32-bit) | `DEFQ` | Not supported | Need to add |
| Define space | `DEFS n` | `DS n` | **Both support** |
| Conditional | `IF cond` | `IF cond` | **Both support** |
| End conditional | `ENDIF` | `ENDIF` | **Both support** |

### Labels

| Feature | z88dk | um80 | Notes |
|---------|-------|------|-------|
| Global labels | `name:` or `name` | `name:` at col 1 | Similar |
| Local labels (dot prefix) | `.name` | Not supported | Need to add |
| Public export shorthand | Not used | `name::` | um80 extension |

### Instructions

Both assemblers support standard Z80 mnemonics. The main differences are:

| Feature | z88dk | um80 | Notes |
|---------|-------|------|-------|
| Case sensitivity | Case insensitive | Case insensitive | **Both** |
| Alternate registers | `af'`, `bc'`, etc. | `AF'`, `BC'`, etc. | **Both** |
| Index registers | `ix`, `iy` | `IX`, `IY` | **Both** |

### Comments

| Feature | z88dk | um80 | Notes |
|---------|-------|------|-------|
| Semicolon comment | `; comment` | `; comment` | **Both support** |

### Expression Operators

| Operator | z88dk | um80 | Notes |
|----------|-------|------|-------|
| Logical OR | `\|` | `OR` | Different syntax |
| Logical AND | `&` | `AND` | Different syntax |
| Bitwise OR | `\|` | `OR` | Same as logical in z88dk |
| Addition | `+` | `+` | **Both** |
| Subtraction | `-` | `-` | **Both** |
| Multiplication | `*` | `*` | **Both** |
| Division | `/` | `/` | **Both** |
| Modulo | `%` or `MOD` | `MOD` | Different |
| Shift left | `<<` | `SHL` | Different |
| Shift right | `>>` | `SHR` | Different |
| HIGH byte | `HIGH expr` | `HIGH(expr)` | **Both** (um80 also supports function syntax) |
| LOW byte | `LOW expr` | `LOW(expr)` | **Both** |

---

## Changes Required in um80

### 1. Number Format Extensions

**File: `um80/um80.py`, function `parse_number()`**

Add support for:

```python
def parse_number(self, s):
    """Parse a numeric constant, return (value, success)."""
    s = s.strip()
    if not s:
        return (0, False)

    # NEW: Check for $xxxx hex notation BEFORE stripping $
    if s.startswith('$') and len(s) > 1:
        try:
            return (int(s[1:], 16), True)
        except ValueError:
            pass  # Fall through to other formats

    # NEW: Check for 0x prefix (C-style hex)
    if s.upper().startswith('0X') and len(s) > 2:
        try:
            return (int(s[2:], 16), True)
        except ValueError:
            return (0, False)

    # NEW: Check for % prefix (binary)
    if s.startswith('%') and len(s) > 1:
        try:
            return (int(s[1:], 2), True)
        except ValueError:
            return (0, False)

    # Existing code continues...
    s = s.upper()
    # DRI extension: strip $ digit separators (e.g., 010$0000B)
    # Only strip $ if NOT at position 0 (already handled above)
    s = s.replace('$', '')
    # ... rest of existing code
```

### 2. New Directives

**Add SECTION directive (maps to CSEG/DSEG)**

```python
if operator == 'SECTION':
    # Map z88dk SECTION to CSEG (code sections) or DSEG (data sections)
    section_name = operands.upper() if operands else ''
    if 'DATA' in section_name or 'BSS' in section_name:
        # Treat as data segment
        self.current_seg = 'DSEG'
    else:
        # Default to code segment
        self.current_seg = 'CSEG'
    if self.pass_num == 2:
        self.output.write_set_location(self.seg_type, self.loc)
    return True
```

**Add EXTERN as alias for EXTRN**

```python
if operator in ('EXTRN', 'EXTERN'):
    # existing EXTRN handling code
```

**Add DEFC directive**

```python
if operator == 'DEFC':
    # DEFC name = value  ->  name EQU value
    # Parse "name = value" from operands
    if '=' in operands:
        parts = operands.split('=', 1)
        name = parts[0].strip()
        value_str = parts[1].strip()
        val, seg, ext, ext_name = self.parse_expression(value_str)
        sym = self.lookup_symbol(name)
        sym.value = val
        sym.defined = True
        sym.segment = seg
    return True
```

**Add DEFQ directive (32-bit data)**

```python
if operator == 'DEFQ':
    # Define quad - 32-bit value (little-endian)
    ops = self.split_operands(operands)
    for op in ops:
        val, seg, ext, ext_name = self.parse_expression(op.strip())
        if self.pass_num == 2:
            self.emit_byte(val & 0xFF)
            self.emit_byte((val >> 8) & 0xFF)
            self.emit_byte((val >> 16) & 0xFF)
            self.emit_byte((val >> 24) & 0xFF)
        else:
            self.loc += 4
    return True
```

### 3. Local Label Support (Dot-Prefix Labels)

Local labels in z88dk start with a dot (`.label`) and are scoped to the enclosing global label.

**Changes needed:**

1. Track current global label scope
2. When encountering `.name`, expand to `current_global$.name` internally
3. Ensure local labels don't conflict across different global label scopes

```python
# In Assembler class __init__:
self.current_global_label = None

# In parse_line, when a global label is defined:
if label and not label.startswith('.'):
    self.current_global_label = label

# When referencing labels:
def expand_local_label(self, name):
    if name.startswith('.'):
        if self.current_global_label:
            return f"{self.current_global_label}${name[1:]}"
        else:
            return name[1:]  # Remove dot if no global context
    return name
```

### 4. Expression Operator Extensions

**Add C-style operators as alternatives:**

```python
# In expression parsing, add these operator mappings:
OPERATOR_ALIASES = {
    '|': 'OR',
    '&': 'AND',
    '<<': 'SHL',
    '>>': 'SHR',
    '%': 'MOD',
}

# Before parsing operators, replace C-style with MACRO-80 style
for c_op, m80_op in OPERATOR_ALIASES.items():
    expr = expr.replace(c_op, f' {m80_op} ')
```

### 5. Conditional Assembly Extensions

Add CPU-specific predefined symbols that z88dk uses:

```python
# Predefined symbols for z88dk compatibility
self.define_symbol('__CPU_Z80__', 1)
self.define_symbol('__CPU_Z180__', 0)
self.define_symbol('__CPU_Z80N__', 0)
self.define_symbol('__CPU_RABBIT__', 0)
self.define_symbol('__CPU_KC160__', 0)
self.define_symbol('__CPU_EZ80__', 0)
```

---

## Implementation Priority

### Phase 1: Essential (Required for basic z88dk math library)

1. **$xxxx hex notation** - Very common in z88dk
2. **0xXXXX hex notation** - Used for float constants in DEFQ
3. **EXTERN alias** - Simple change
4. **DEFQ directive** - Required for 32-bit float constants
5. **SECTION directive** - Map to CSEG/DSEG

### Phase 2: Useful (Improves compatibility)

6. **%binary notation** - Occasional use
7. **DEFC directive** - Convenient constant definition
8. **Local labels (.name)** - Used for internal branch targets

### Phase 3: Complete (Full compatibility)

9. **Expression operators (|, &, <<, >>)** - For conditional assembly
10. **CPU predefined symbols** - For conditional compilation

---

## Alternative: Use Existing Converter

The file `/home/wohl/src/uada80/tools/z88dk_to_um80.py` already handles most conversions:

- SECTION -> removed (CSEG added at file start)
- EXTERN -> EXTRN
- $xx -> 0xxH
- defw/defb/defs -> DW/DB/DS
- Lowercase -> UPPERCASE

**Missing from converter:**
- DEFQ support
- 0xXXXX hex notation
- Local label (.name) handling
- DEFC directive

The converter could be extended, or um80 could be modified to support z88dk syntax directly.

---

## Recommended Approach

1. **Short term**: Extend the existing converter to handle DEFQ and 0x notation
2. **Long term**: Add Phase 1 features to um80 for native z88dk support

This allows immediate use of z88dk math libraries while working toward native support.
