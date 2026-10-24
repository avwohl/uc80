/* demo.c - a tinycurses demo: a walker on a bordered field, with a
 * status window.  The keys come from stdin (input.txt): h j k l move,
 * c clears the field, x writes a mark, q quits.  The output is the
 * stream of VT100 escape sequences that tinycurses writes, so it shows
 * that the screen code works the same on every compiler.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 */
#include <stdio.h>
#include <string.h>
#include "tinycurses.h"

#define TOP 2
#define LEFT 1
#define ROWS 12
#define COLSN 40

static int py = 5, px = 20, marks, moves;

static void field(void)
{
    int y;

    mvaddch(TOP - 1, LEFT - 1, '+');
    mvaddch(TOP - 1, LEFT + COLSN, '+');
    mvaddch(TOP + ROWS, LEFT - 1, '+');
    mvaddch(TOP + ROWS, LEFT + COLSN, '+');
    for (y = 0; y < COLSN; y++) {
        mvaddch(TOP - 1, LEFT + y, '-');
        mvaddch(TOP + ROWS, LEFT + y, '-');
    }
    for (y = 0; y < ROWS; y++) {
        mvaddch(TOP + y, LEFT - 1, '|');
        mvaddch(TOP + y, LEFT + COLSN, '|');
    }
}

static void status(WINDOW *w)
{
    werase(w);
    box(w, 0, 0);
    mvwprintw(w, 1, 2, "pos %2d,%-2d", py, px);
    mvwprintw(w, 2, 2, "moves %d", moves);
    mvwprintw(w, 3, 2, "marks %03d", marks);
    wrefresh(w);
}

int main(void)
{
    WINDOW *sw;
    int c, y, x;

    initscr();
    cbreak();
    noecho();
    nonl();
    clear();
    attron(A_STANDOUT);
    mvaddstr(0, 0, " tinycurses demo ");
    attroff(A_STANDOUT);
    mvprintw(0, 30, "LINES=%d COLS=%d", LINES, COLS);
    field();
    sw = newwin(5, 16, 3, 50);
    mvaddch(TOP + py, LEFT + px, '@');
    status(sw);
    for (;;) {
        c = getch();
        if (c == 'q' || c == ERR)
            break;
        y = py;
        x = px;
        if (c == 'h' && px > 0)
            px--;
        else if (c == 'l' && px < COLSN - 1)
            px++;
        else if (c == 'k' && py > 0)
            py--;
        else if (c == 'j' && py < ROWS - 1)
            py++;
        else if (c == 'x') {
            mvaddch(TOP + py, LEFT + px, '*' | A_STANDOUT);
            marks++;
        } else if (c == 'c') {
            for (y = 0; y < ROWS; y++) {
                move(TOP + y, LEFT);
                for (x = 0; x < COLSN; x++)
                    addch(' ');
            }
            marks = 0;
            y = py;
            x = px;
        } else
            continue;
        moves++;
        if ((mvinch(TOP + y, LEFT + x) & 127) == '@')
            mvaddch(TOP + y, LEFT + x, '.');
        mvaddch(TOP + py, LEFT + px, '@');
        status(sw);
    }
    mvprintw(TOP + ROWS + 2, 0, "bye after %d moves", moves);
    clrtoeol();
    refresh();
    delwin(sw);
    endwin();
    return 0;
}
