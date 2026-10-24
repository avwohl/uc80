/* advdata.c - the text of NetBSD adventure, as a RAM array of the same size.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 *
 * NetBSD builds data.c from the file "glorkz" (56,375 bytes of game text,
 * BSD license) with the host tool "mkdata".  The array data_file[] then has
 * 55,716 bytes (the file, lightly encrypted, with leading blanks of a line
 * packed into one tab).  The benchmark does not store or generate that
 * text.  Every compiler gets this array of the same size, in the BSS, so
 * the program takes the same RAM with every compiler and nothing differs
 * between compilers.  The program is built for size only and never runs.
 */
char data_file[55716];
