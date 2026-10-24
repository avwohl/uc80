#!/usr/bin/env python3
"""Make bench/programs.d/games_big.toml (the size-only "does it fit" games).

SPDX-License-Identifier: GPL-3.0-or-later

Usage: gen_toml.py NETBSD_CHECKOUT ROGUE_CHECKOUT MOJOZORK_CHECKOUT

Each checkout is a git clone at the pinned commit below.  The script reads
the files, computes their sha256 and writes the manifest.  The manifest
fetches the same files from raw.githubusercontent.com at the pinned commit
(nothing from the upstream projects is stored in this repository).  The
"edit" entries are generated from the include lines of each file, so that
every compiler compiles the same text.  Program-specific edits are in the
EXTRA tables below.
"""
import hashlib
import re
import sys
from pathlib import Path

NETBSD = "5b0e0d5997518246c017c6577adcb59be12d0a79"
ROGUE = "f36912c4744b83ca79f9f5489a0528a2f0683996"
MOJO = "ff7e00742a00acec8e175ddefb97520fb270df2d"

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / "programs.d" / "games_big.toml"


def q(s):
    """A TOML basic string."""
    return '"' + (s.replace("\\", "\\\\").replace('"', '\\"')
                  .replace("\n", "\\n").replace("\t", "\\t")) + '"'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# System headers that a game includes and that the stub replaces or drops.
DROP = {"sys/types.h", "sys/time.h", "sys/stat.h", "sys/file.h",
        "sys/endian.h", "endian.h", "unistd.h", "time.h", "signal.h",
        "err.h", "pwd.h", "fcntl.h", "termios.h", "sys/param.h",
        "sys/wait.h", "errno.h", "setjmp.h"}
INC = re.compile(r"^[ \t]*#[ \t]*include[ \t]*<([^>]+)>", re.M)


def include_edits(fname, text, stub, keep=()):
    edits = []
    for m in INC.finditer(text):
        h = m.group(1)
        line = m.group(0).strip()
        # the edit text is the include line without leading blanks
        line = "#include <%s>" % h if line.replace(" ", "").replace("\t", "") \
            == "#include<%s>" % h else line
        if text.count(m.group(0)) != 1:
            continue
        if h == "sys/cdefs.h":
            edits.append((fname, m.group(0), '#include "%s"' % stub))
        elif h == "curses.h":
            edits.append((fname, m.group(0), '#include "tinycurses.h"'))
        elif h in DROP and h not in keep:
            edits.append((fname, m.group(0), ""))
    return edits


def emit_program(out, name, about, files, edits, defines=(), stdin=None,
                 main=None, extra_files=(), kind="size-only"):
    out.append("[[program]]")
    out.append("name = %s" % q(name))
    out.append("tier = 3")
    if kind:
        out.append("kind = %s" % q(kind))
    out.append("about = %s" % q(about))
    if defines:
        out.append("time_defines = [%s]" % ", ".join(q(d) for d in defines))
        out.append("check_defines = [%s]" % ", ".join(q(d) for d in defines))
    out.append("files = [")
    for f in files:
        ismain = ", main = true" if f["name"] == main else ""
        out.append("  { repo = %s, path = %s, sha256 = %s%s },"
                   % (q(f["repo"]), q(f["path"]), q(f["sha256"]), ismain))
    for e in extra_files:
        out.append("  { shared = %s%s }," % (
            q(e[0]), ", main = true" if e[1] else ""))
    out.append("]")
    if edits:
        out.append("edit = [")
        for f, o, n in edits:
            out.append("  { file = %s, old = %s, new = %s },"
                       % (q(f), q(o), q(n)))
        out.append("]")
    out.append("")


def netbsd_files(checkout, game, names):
    res = []
    for n in names:
        p = Path(checkout) / "games" / game / n
        res.append({"name": n, "repo": "netbsd", "path": "games/%s/%s" % (game, n),
                    "sha256": sha(p), "text": p.read_text(encoding="latin-1")})
    return res


def sources(checkout, game, skip=()):
    d = Path(checkout) / "games" / game
    return sorted(p.name for p in d.iterdir()
                  if p.suffix in (".c", ".h") and p.name not in skip)


SHIM = [("support/posix_shim.c", False), ("support/posix_shim.h", False)]
BGSTUB = [("games_big/bgstub.h", False), ("games_big/bgstub.c", False)]

# Program-specific edits: (file, old, new).
NETBSD_PROGS = {
    "monop": dict(
        about="NetBSD monop (4.1BSD Monopoly), 4k lines; build only, size per object",
        main="monop.c",
        edits=[
            ("execute.c", "typedef\tstruct tm\tTIME;\n", ""),
            ("execute.c", "stat(buf, &sb)", "bg_st(buf, &sb)"),
            ("execute.c", "fstat(fileno(inf), &sbuf)", "bg_fz(fileno(inf), &sbuf)"),
            ("execute.c", "sizeof(play[0])", "sizeof(*play)"),
            # HI-TECH C 3.09 cpp does not expand the first use of CC_D
            ("cards.c", "\tCC_D.info = cc_cards;", "\tdeck[0].info = cc_cards;"),
            # HI-TECH C 3.09 puts a label in the name space of the function err()
            ("houses.c", "result[1] - result[2]) > 1)) {\nerr:", "result[1] - result[2]) > 1)) {\nerr_l:"),
            ("houses.c", "abs(result[0] - result[1]) > 1)\n\t\tgoto err;", "abs(result[0] - result[1]) > 1)\n\t\tgoto err_l;"),
            ("houses.c", "temp[1] - temp[2]) > 1)) {\nerr:", "temp[1] - temp[2]) > 1)) {\nerr_l:"),
            ("houses.c", "abs(temp[0] - temp[1]) > 1)\n\t\tgoto err;", "abs(temp[0] - temp[1]) > 1)\n\t\tgoto err_l;"),
            ("cards.c", "\t\tdp = &CC_D;", "\t\tdp = &deck[0];"),
            # uc80 whole-program mode joins file-scope statics of the same name
            ("houses.c", '#include "bgstub.h"', '#include "bgstub.h"\n#define names hs_names'),
            ("execute.c", '#include "bgstub.h"', '#include "bgstub.h"\n#define buf ex_buf'),
            ("cards.c", "sizeof(dp->cards[0])", "sizeof(*dp->cards)"),
            ("monop.c", "\t/* Revoke setgid privileges */\n\tsetgid(getgid());\n\n", ""),
        ]),
    "trek": dict(
        about="NetBSD trek (Eric Allman's Star Trek, 1BSD), 7.9k lines; build only, size per object",
        main="main.c", edits=[]),
    "adventure": dict(
        about="NetBSD adventure (Crowther and Woods 350-point Adventure, Gillogly's C port); text replaced by a same-size array; build only",
        main="main.c", skip=("setup.c",), edits=[],
        extra=[("games_big/advdata.c", False)]),
    "battlestar": dict(
        about="NetBSD battlestar (4.3BSD adventure game), 8.6k lines, 120 KB of room data; build only, size per object",
        main="battlestar.c", edits=[
            # sig_t is a function pointer in the original; the shim's signal() uses void *
            # sccz80 does not take sizeof of a subscripted member
            ("save.c", "fread(location[n].link, sizeof location[n].link, 1, fp);",
             "fread(location[n].link, sizeof(location[n].link), 1, fp);"),
            ("save.c", "fread(location[n].objects, sizeof location[n].objects, 1, fp);",
             "fread(location[n].objects, sizeof(location[n].objects), 1, fp);"),
            ("save.c", "fwrite(location[n].link, sizeof location[n].link, 1, fp);",
             "fwrite(location[n].link, sizeof(location[n].link), 1, fp);"),
            ("save.c", "fwrite(location[n].objects, sizeof location[n].objects, 1, fp);",
             "fwrite(location[n].objects, sizeof(location[n].objects), 1, fp);"),
            ("fly.c", "static sig_t   oldsig;", "static void *oldsig;"),
            ("fly.c", "(*oldsig) (SIGINT);", "((void (*)(int))oldsig)(SIGINT);"),
        ],
        extra=[("support/tinycurses.c", False), ("support/tinycurses.h", False)]),
}


def repo_files(checkout, repo, names, prefix=""):
    res = []
    for n in names:
        p = Path(checkout) / prefix / n
        res.append({"name": n, "repo": repo, "path": prefix + n,
                    "sha256": sha(p), "text": p.read_text(encoding="latin-1")})
    return res


ROGUE_SKIP = ("mdport.c", "state.c", "xcrypt.c", "mdport.h")
ROGUE_EDITS = [
    ("rogue.h", '#include "mdport.h"', '#include "rgstub.h"'),
    # a block-scope "extern char **environ;" gets no EXTRN from uc80; the
    # declaration is at file scope in rgstub.h
    ("save.c", "    extern char **environ;\n", ""),
    # the two K&R definitions that C23 removed
    ("fight.c", "prname(who, upper)\nchar *who;\nint upper;\n",
     "prname(char *who, int upper)\n"),
    ("init.c", "#define NCOLORS (sizeof rainbow / sizeof (char *))",
     "#define NCOLORS (sizeof(rainbow) / sizeof(char *))"),
    ("main.c", "\nmain(argc, argv, envp)\nchar **argv;\nchar **envp;\n",
     "\nint\nmain(int argc, char **argv, char **envp)\n"),
]


def main():
    netbsd, rogue, mojo = sys.argv[1:4]
    out = [open(HERE / "toml_head.txt").read().rstrip("\n"), ""]
    for name, spec in NETBSD_PROGS.items():
        game = spec.get("game", name)
        names = sources(netbsd, game, spec.get("skip", ()))
        files = netbsd_files(netbsd, game, names)
        edits = []
        for f in files:
            edits += include_edits(f["name"], f["text"], "bgstub.h")
        edits += spec["edits"]
        emit_program(out, "%s-size" % name, spec["about"], files, edits,
                     main=spec["main"], extra_files=SHIM + BGSTUB + spec.get("extra", []))
    names = sorted(p.name for p in Path(rogue).iterdir()
                   if p.suffix in (".c", ".h") and p.name not in ROGUE_SKIP)
    files = repo_files(rogue, "rogue36", names)
    edits = []
    for f in files:
        edits += include_edits(f["name"], f["text"], "rgstub.h")
        for m in re.finditer(r'^#include "curses.h"', f["text"], re.M):
            pass
    edits += ROGUE_EDITS
    emit_program(out, "rogue36-size",
                 "Rogue 3.6 (1980-81), 13k lines, tinycurses; mdport, state and xcrypt replaced by small stand-ins; build only",
                 files, edits, main="main.c",
                 extra_files=SHIM + BGSTUB + [
                     ("support/tinycurses.c", False),
                     ("support/tinycurses.h", False),
                     ("games_big/curses.h", False),
                     ("games_big/rgstub.h", False),
                     ("games_big/rgmd.c", False)])
    files = repo_files(mojo, "mojozork", ["mojozork.c"])
    edits = [("mojozork.c", "static inline void dbg(", "static void dbg("),
             ("mojozork.c", "#include <stdio.h>", '#include "bgstub.h"'),
             ("mojozork.c", "#include <time.h>\n", "")]
    emit_program(out, "mojozork-size",
                 "mojozork (Z-machine interpreter, plays Zork I), 2.2k lines; build only, FILE calls go to the bgstub stand-ins",
                 files, edits, main="mojozork.c",
                 extra_files=SHIM + BGSTUB)
    OUT.write_text("\n".join(out))


if __name__ == "__main__":
    main()
