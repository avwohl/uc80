"""``-g``: write ``;#`` debug markers into the assembler text.

The markers follow ``docs/debug_info.md`` of um80_and_friends.  They are
whole-line comments.  The code of a program is the same bytes with and
without ``-g`` (docs/debug_info_g.md); this module only appends comment
lines to ``ctx.lines`` and never reads the generator state that decides
what code to write.

The position of a statement is the file and line of its first token, found
through ``srcmap`` (the line of a token in the preprocessed text is not the
line in the file).  A node that has no token (``return;``, ``break;``) takes
the line of its ``pos`` and the file of the last token seen.
"""

from __future__ import annotations

import difflib
from contextlib import contextmanager

from . import srcmap
from .markers import is_marker

_SIMPLE = (str, int, float, bool, bytes)


def first_token(nodes, limit: int = 4000):
    """The first token (by line and column) that has a source map, in the
    nodes ``nodes`` (a node, a list, or None).  The walk stops after
    ``limit`` nodes."""
    best = None
    seen = 0
    stack = list(nodes) if isinstance(nodes, (list, tuple)) else [nodes]
    while stack and seen < limit:
        n = stack.pop()
        seen += 1
        if n is None or isinstance(n, _SIMPLE):
            continue
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if hasattr(n, "file_id") and hasattr(n, "line") and hasattr(n, "text"):
            key = (n.line, getattr(n, "column", 0))
            if (n.file_id in srcmap._MAPS and n.line
                    and (best is None or key < best[0])):
                best = (key, n)
            continue
        d = getattr(n, "__dict__", None)
        if d is None:
            d = {f: getattr(n, f, None)
                 for f in getattr(n, "__dataclass_fields__", ())}
        for k, v in d.items():
            if k != "pos":
                stack.append(v)
    return best[1] if best else None


def strip_markers(lines: list[str]):
    """(the lines without markers, [(index in that list, marker)]): the
    marker goes in front of the line that has that index."""
    clean: list[str] = []
    marks: list[tuple[int, str]] = []
    for ln in lines:
        if is_marker(ln):
            marks.append((len(clean), ln))
        else:
            clean.append(ln)
    return clean, marks


def restore_markers(before: list[str], after: list[str], marks) -> list[str]:
    """``after`` is ``before`` as a text pass changed it.  Put the markers
    back: each one goes in front of the line of ``after`` that is its old
    line, or in front of the first line that the pass wrote in its place
    (the line is then possibly not the right one, the code is the same)."""
    if not marks:
        return list(after)
    sm = difflib.SequenceMatcher(None, before, after, autojunk=False)
    new_at: list[int] = [len(after)] * (len(before) + 1)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                new_at[i1 + k] = j1 + k
        else:
            # replaced or deleted: spread the old lines over the new ones
            for k in range(i1, i2):
                new_at[k] = j1 + (k - i1) * (j2 - j1) // max(i2 - i1, 1)
    # an old index with no line (the end) maps to the end
    out: list[str] = []
    by_pos: dict[int, list[str]] = {}
    for k, text in marks:
        by_pos.setdefault(new_at[k] if k < len(before) else len(after),
                          []).append(text)
    for j, ln in enumerate(after):
        out.extend(by_pos.get(j, ()))
        out.append(ln)
    out.extend(by_pos.get(len(after), ()))
    return out


class DebugMarkersMixin:
    """The marker writer of the code generator (``debug_info`` is the
    switch).  Every method returns at once when ``-g`` is off."""

    debug_info = False

    # -- state ---------------------------------------------------------

    def _dbg_reset(self) -> None:
        self._dbg_files: dict[str, int] = {}
        self._dbg_fid = 0           # file id of the last token seen
        self._dbg_cur = None        # (file number, line) of the last marker
        self._dbg_hoisted: dict[int, object] = {}   # id(node) -> loop
        self._dbg_inline_marks: dict[int, tuple] = {}
        self._dbg_hoist_where: dict[int, object] = {}
        self._dbg_keep: list = []   # keeps the nodes of the two tables alive
        self._dbgv_reset()          # the variables (debug_vars.py)

    def _dbg_file_no(self, path: str) -> int:
        no = self._dbg_files.get(path)
        if no is None:
            no = len(self._dbg_files) + 1
            self._dbg_files[path] = no
        return no

    def _dbg_where(self, header, node=None):
        """(path, line) of the first token of ``header``; when it has no
        token, of the ``pos`` line of ``node``; else None."""
        tok = first_token(header)
        if tok is not None:
            return self._dbg_with_outer(srcmap.locate_pair(tok),
                                        srcmap.outer_pair(tok))
        pos = getattr(node, "pos", None)
        line = getattr(pos, "start_line", None)
        if line and self._dbg_fid:
            return self._dbg_with_outer(
                srcmap.locate_at(self._dbg_fid, line),
                srcmap.outer_at(self._dbg_fid, line))
        return None

    @staticmethod
    def _dbg_with_outer(where, outer):
        """(path, line) or, under a ``#line`` directive, (path, line,
        (outer path, outer line))."""
        if where is None or outer is None or outer == tuple(where[:2]):
            return where
        return (where[0], where[1], outer)

    @contextmanager
    def dbg_clean(self, start: int):
        """Run a text pass that works on ``ctx.lines`` from index ``start``
        as it runs without ``-g``: it sees no marker.  The markers come
        back at the lines they were in front of (restore_markers).  With
        ``-g`` off this does nothing."""
        if not self.debug_info:
            yield
            return
        lines = self.ctx.lines
        clean, marks = strip_markers(lines[start:])
        tmp = lines[:start] + clean
        before = list(clean)
        self.ctx.lines = tmp
        try:
            yield
        finally:
            self.ctx.lines = lines
            if tmp[:start] != lines[:start]:
                raise AssertionError("a text pass changed lines before "
                                     "its region")
            lines[start:] = restore_markers(before, tmp[start:], marks)

    # -- markers -------------------------------------------------------

    def _dbg_emit(self, text: str) -> None:
        lines = self.ctx.lines
        if lines and lines[-1] == text:
            return
        n = len(lines)
        lines.append(text)
        dm = getattr(self, "_dm", None)
        if dm is not None and dm.ready and dm.end == n:
            dm.end = n + 1      # a comment line is not code between two items

    def dbg_line_at(self, where, nostmt: bool = False) -> None:
        """``;#line`` for (path, line)."""
        if not self.debug_info or where is None:
            return
        path, line = where[0], where[1]
        no = self._dbg_file_no(path)
        self._dbg_cur = (no, line)
        text = f"\t;#line {no} {line}" + (" nostmt" if nostmt else "")
        if len(where) > 2 and where[2] is not None:
            # D8: the C position below a position set by #line
            text += f" outer={self._dbg_file_no(where[2][0])}:{where[2][1]}"
        self._dbg_emit(text)

    def dbg_line(self, header, node=None, nostmt: bool = False) -> None:
        """``;#line`` for the first token of ``header`` (see _dbg_where)."""
        if not self.debug_info:
            return
        self.dbg_line_at(self._dbg_where(header, node), nostmt)

    def dbg_stmt(self, stmt) -> None:
        """The marker of a statement that gen_statement is about to write.
        A loop writes its own markers (after its labels), and a block, an
        empty statement and a label have no code of their own."""
        if not self.debug_info:
            return
        from . import codegen as cg
        ast = cg.ast
        hoisted = self._dbg_hoisted.get(id(stmt))
        if hoisted is not None:
            self._dbg_hoist_line(hoisted)
            return
        if isinstance(stmt, (ast.CompoundStmt, ast.EmptyStmt, ast.WhileStmt,
                             ast.DoWhileStmt, ast.ForStmt, ast.CaseStmt,
                             ast.DefaultStmt, ast.LabelStmt)):
            return
        if isinstance(stmt, ast.ExpressionStmt):
            if not stmt.expr:
                return
            self.dbg_line(stmt.expr, stmt)
        elif isinstance(stmt, ast.ReturnStmtValue):
            self.dbg_line(stmt.value, stmt)
        elif isinstance(stmt, (ast.IfStmt, ast.IfStmtElse)):
            self.dbg_line(stmt.condition, stmt)
        elif isinstance(stmt, ast.SwitchStmt):
            self.dbg_line(getattr(stmt, "expr", None)
                          or getattr(stmt, "condition", None), stmt)
        elif isinstance(stmt, ast.GotoStmt):
            self.dbg_line(stmt.label, stmt)
        elif isinstance(stmt, ast.AsmDeclaration):
            self.dbg_line(stmt.template, stmt)
        else:                   # return; break; continue;
            self.dbg_line(None, stmt)

    def _dbg_hoist_line(self, loop) -> None:
        """Code that loop invariant motion moved out of ``loop``: the line
        of the loop, and not a step point (D4 of the plan)."""
        cache = self._dbg_hoist_where
        key = id(loop)
        if key not in cache:
            head = [getattr(loop, "init", None), getattr(loop, "condition", None),
                    getattr(loop, "update", None)]
            cache[key] = self._dbg_where(head, loop)
        self.dbg_line_at(cache[key], nostmt=True)

    def note_hoisted(self, items, loop) -> None:
        """loop invariant motion made ``items`` out of code of ``loop``."""
        if not self.debug_info:
            return
        for it in items:
            self._dbg_hoisted[id(it)] = loop
            self._dbg_keep.append(it)
        self._dbg_keep.append(loop)

    def dbg_decl(self, decl) -> None:
        """The marker of a local declaration that has an initializer."""
        if not self.debug_info:
            return
        hoisted = self._dbg_hoisted.get(id(decl))
        if hoisted is not None:
            self._dbg_hoist_line(hoisted)
            return
        from . import codegen as cg
        try:
            has_init = any(init is not None
                           for _n, _f, init, _fn in cg.iter_var_decls(decl))
        except Exception:
            return
        if has_init:
            self.dbg_line(decl.declarators, decl)

    # -- functions -----------------------------------------------------

    def dbg_begin_func(self, func) -> None:
        """A function starts: its tokens say which input file it is in."""
        if not self.debug_info:
            return
        tok = first_token([func.decl_specs, func.declarator])
        if tok is not None:
            self._dbg_fid = tok.file_id

    def dbg_func(self, name: str, func, at: int, frame: str) -> None:
        """Insert ``;#func`` and the declaration line at index ``at`` of
        ``ctx.lines`` (just after the label of the function)."""
        if not self.debug_info:
            return
        where = self._dbg_where([func.decl_specs, func.declarator], func)
        label = f"_{name}"
        sym = self.ctx.globals.get(name)
        if sym is not None:
            label = sym.label()
        if where is None:
            fields = "0 0"
            extra = []
        else:
            no = self._dbg_file_no(where[0])
            fields = f"{no} {where[1]}"
            extra = [f"\t;#line {no} {where[1]}"]
            if len(where) > 2 and where[2] is not None:
                extra[0] += (f" outer={self._dbg_file_no(where[2][0])}"
                             f":{where[2][1]}")
        self.ctx.lines[at:at] = [
            f"\t;#func {label} {fields} frame={frame} pops=0", *extra]

    def dbg_endfunc(self, name: str) -> None:
        if not self.debug_info:
            return
        label = f"_{name}"
        sym = self.ctx.globals.get(name)
        if sym is not None:
            label = sym.label()
        self.ctx.lines.append(f"\t;#endfunc {label}")

    def dbg_closing_brace(self, func) -> None:
        """The epilogue has the line of the closing brace."""
        if not self.debug_info:
            return
        pos = getattr(func.body, "pos", None) or getattr(func, "pos", None)
        line = getattr(pos, "end_line", None)
        if line and self._dbg_fid:
            self.dbg_line_at(self._dbg_with_outer(
                srcmap.locate_at(self._dbg_fid, line),
                srcmap.outer_at(self._dbg_fid, line)))

    def dbg_file_table(self) -> list[str]:
        """The ``;#file`` lines of the files used."""
        out = []
        for path, no in sorted(self._dbg_files.items(), key=lambda kv: kv[1]):
            esc = path.replace("\\", "\\\\").replace('"', '\\"')
            out.append(f';#file {no} "{esc}"')
        return out

    # -- inlined bodies ------------------------------------------------

    def dbg_note_inline(self, body, name: str, call, func) -> None:
        """The analyzer inlined ``func`` at ``call``: ``body`` is the tree
        that replaced the call."""
        if not self.debug_info:
            return
        site = self._dbg_where(call)
        if site is None:
            return
        callee = self._dbg_where(func.body.items[0] if func.body.items
                                 else func, func)
        self._dbg_inline_marks[id(body)] = (name, site, callee)
        self._dbg_keep.append(body)

    def dbg_carry_inline(self, old, new) -> None:
        """The analyzer made ``new`` as a copy of ``old``."""
        info = self._dbg_inline_marks.get(id(old))
        if info is not None:
            self._dbg_inline_marks[id(new)] = info
            self._dbg_keep.append(new)

    def _dbg_wrap_expr(self, fn):
        """``fn(expr, ...)`` with ``;#inline`` and ``;#endinline`` around
        the code of an inlined body."""
        marks = self._dbg_inline_marks

        def wrapped(expr, *a, **k):
            info = marks.get(id(expr))
            if info is None:
                return fn(expr, *a, **k)
            name, site, callee = info
            no = self._dbg_file_no(site[0])
            self._dbg_emit(f"\t;#inline {no} {site[1]} {name}")
            if callee is not None:
                cno = self._dbg_file_no(callee[0])
                self._dbg_emit(f"\t;#line {cno} {callee[1]}")
            try:
                return fn(expr, *a, **k)
            finally:
                self._dbg_emit("\t;#endinline")
        return wrapped

    def dbg_install(self) -> None:
        """Wrap the expression entry points (on this instance only)."""
        for nm in ("gen_expr", "gen_expr_discard", "gen_expr_to_de",
                   "_gen_expr_a", "_gen_cond_false_jump",
                   "_gen_condition_value"):
            setattr(self, nm, self._dbg_wrap_expr(getattr(self, nm)))
