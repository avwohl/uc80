"""The ROM (freestanding) target: which library modules it may link, the
start-up code it embeds, and the checks that keep CP/M-only calls out.

See docs/rom.md.  The classification here is used twice: build_libs.py
builds libc_rom.lib from the modules it accepts, and the compiler refuses a
program that calls a library function the ROM library does not have.
"""

import re
from pathlib import Path

# libc modules that never reach the BDOS but still make no sense in a ROM:
# process control, the environment, the clock, the heap, and the state of
# the stream functions.  Everything else that does not reach the BDOS is in
# libc_rom.lib.
ROM_EXCLUDED = frozenset({
    "lc_data",          # stdin, stdout and stderr
    "lc_exit", "lc_exit_data", "lc_atexit",
    "lc_system", "lc_getenv",
    "lc_time", "lc_clock",
    "lc_malloc_data", "lc_free",   # the heap: malloc itself reaches the BDOS
    "lc_tmpnam", "lc_remove_data",
    "lc_setbuf", "lc_setvbuf",
    "lc_feof", "lc_ferror", "lc_clearerr", "lc_ftell", "lc_fgetpos",
    "lc_ungetc",
})

_BDOS_USE = re.compile(r"\b(CALL|JP|JR)\s+(BDOS|5)\b|\(\s*0*6H?\s*\)", re.I)


def _scan(lib_dir: Path):
    """{module key: (publics, externs, uses_bdos)} for lc/ and rt/."""
    mods = {}
    for sub in ("lc", "rt"):
        d = lib_dir / sub
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.mac")):
            text = path.read_text(errors="replace")
            pubs, exts = set(), set()
            for m in re.finditer(r"^\s+(PUBLIC|EXTRN)\s+([^;\n]+)", text,
                                 re.I | re.M):
                names = {n.strip() for n in m.group(2).split(",") if n.strip()}
                (pubs if m.group(1).upper() == "PUBLIC" else exts).update(names)
            mods[f"{sub}/{path.stem}"] = (pubs, exts - pubs,
                                          bool(_BDOS_USE.search(text)))
    return mods


def rom_modules(lib_dir: Path):
    """Return (safe, unsafe): module keys ("lc/lc_strlen") the ROM library
    may contain, and those it must not.  A module is unsafe when it calls the
    BDOS, is on ROM_EXCLUDED, needs a symbol no module defines, or needs a
    symbol an unsafe module defines."""
    mods = _scan(lib_dir)
    defs = {}
    for key, (pubs, _e, _b) in mods.items():
        for s in pubs:
            defs.setdefault(s, []).append(key)
    unsafe = {k for k, (_p, _e, bd) in mods.items()
              if bd or k.split("/")[1] in ROM_EXCLUDED}
    changed = True
    while changed:
        changed = False
        for key, (_p, exts, _b) in mods.items():
            if key in unsafe:
                continue
            for s in exts:
                if s not in defs or any(g in unsafe for g in defs[s]):
                    unsafe.add(key)
                    changed = True
                    break
    return set(mods) - unsafe, unsafe


def rom_unavailable_symbols(lib_dir: Path) -> set:
    """Upper-case PUBLIC names of libc that libc_rom.lib lacks."""
    mods = _scan(lib_dir)
    safe, unsafe = rom_modules(lib_dir)
    have = {s.upper() for k in safe for s in mods[k][0]}
    return {s.upper() for k in unsafe if k.startswith("lc/")
            for s in mods[k][0]} - have


def undefined_in_rom(lib_dir: Path, code: str) -> list:
    """Names (without the leading underscore) of library functions the
    generated assembly references that libc_rom.lib does not have."""
    bad = rom_unavailable_symbols(lib_dir)
    found = set()
    for m in re.finditer(r"^\s*EXTRN\s+([^;\n]+)", code, re.I | re.M):
        for n in m.group(1).split(","):
            n = n.strip()
            if n.upper() in bad and not n.startswith("__"):
                found.add(n[1:] if n.startswith("_") else n)
    return sorted(found)


# ---- start-up code -----------------------------------------------------

_BLOCK = re.compile(r"^;@(INT|NMI)_BEGIN\n.*?^;@\1_END\n", re.S | re.M)


def startup_text(path: Path, int_handler: bool, nmi_handler: bool,
                 stack_top: str | None) -> list:
    """Lines of crt0_rom.mac for embedding.  A user handler (PUBLIC
    __int_handler or __nmi_handler) replaces the default stub at its vector;
    stack_top (an assembler expression) replaces __RAM_END."""
    text = path.read_text()
    use = {"INT": int_handler, "NMI": nmi_handler}

    def block(m):
        body = m.group(0).splitlines()[1:-1]
        user = [ln for ln in body if ln.startswith(";@USER")]
        keep = ([ln[len(";@USER"):] for ln in user] if use[m.group(1)]
                else [ln for ln in body if not ln.startswith(";@USER")])
        return "\n".join(keep) + "\n"

    text = _BLOCK.sub(block, text)
    if stack_top:
        text = text.replace("LD\tSP,__RAM_END", f"LD\tSP,{stack_top}")
    out = []
    for line in text.splitlines():
        s = line.strip().upper()
        if s in {".Z80", "DSEG"} or s.startswith("END"):
            continue
        if s.startswith("EXTRN") and "_MAIN" in s and "__" not in s:
            continue
        out.append(line)
    return out


_PRINTF_TABLES = re.compile(
    r"\n; Printf format dispatch tables[^\n]*\n\n"
    r"\tpublic\t__printf_format_table\n__printf_format_table:\n"
    r"\tdb\t0\t\t; sentinel\n\n"
    r"\tpublic\t__printf_long_table\n__printf_long_table:\n"
    r"\tdb\t0\t\t; sentinel\n\n"
    r"\tpublic\t__printf_ll_table\n__printf_ll_table:\n"
    r"\tdb\t0\t\t; sentinel\n__printf_ll_entry:\n\tret\n")


def strip_empty_printf_tables(code: str) -> str:
    """Remove the empty printf dispatch tables the compiler writes beside
    main (7 bytes of ROM), when no printf format asked for an entry."""
    return _PRINTF_TABLES.sub("\n", code)


def link_command(output: str = "prog") -> str:
    """The ul80 command line for a ROM built from one module."""
    return (f"ul80 --bin -p 0 -d F800 --rom-data --rom-size 8000 "
            f"--ram-size 800 --min-stack 100 -o {output}.bin -S {output}.sym "
            f"{output}.rel libc_rom.lib runtime.lib")
