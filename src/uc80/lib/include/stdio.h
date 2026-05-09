/* stdio.h - Standard I/O for uc80/CP-M */
#ifndef _STDIO_H
#define _STDIO_H

/* Size type — track pointer width (matches stddef.h).  Under --int=32
 * --ptr=16 this stays a 16-bit unsigned short so libc's fread/fwrite
 * receive their length args at the offsets they were assembled for.  */
#ifndef _SIZE_T_DEFINED
#define _SIZE_T_DEFINED
#if __SIZEOF_POINTER__ == __SIZEOF_INT__
typedef unsigned int size_t;
#elif __SIZEOF_POINTER__ == __SIZEOF_LONG__
typedef unsigned long size_t;
#elif __SIZEOF_POINTER__ == __SIZEOF_SHORT__
typedef unsigned short size_t;
#endif
#endif

/* GCC internal types used by some test suites */
#ifndef __intptr_t
typedef int __intptr_t;
#endif

/* NULL pointer */
#ifndef NULL
#define NULL ((void *)0)
#endif

/* EOF indicator */
#define EOF (-1)

/* File type - opaque structure for CP/M
 * Actual structure defined in libc.mac:
 *   0: fd (1) - slot number (0-2 = stdin/out/err, 3+ = files)
 *   1: flags (1) - bit0=open, bit1=EOF, bit2=error, bit3=write, bit4=read
 *   2: unget (1) - ungetc char (0xFF = none)
 *   3: bufpos (1) - current buffer position
 *   4: bufcnt (1) - valid bytes in buffer
 *   5: (reserved)
 *   6-41: FCB (36 bytes) - CP/M File Control Block
 *   42-169: buffer (128 bytes) - I/O buffer
 * Total: 170 bytes per FILE
 */
typedef struct _FILE FILE;

/* Standard streams */
extern FILE *stdin;
extern FILE *stdout;
extern FILE *stderr;

/* Standard I/O functions */
int putchar(int c);
int getchar(void);
int puts(const char *s);
char *gets(char *s);
int printf(const char *format, ...);
int fprintf(FILE *stream, const char *format, ...);

/* String I/O */
int sprintf(char *str, const char *format, ...);
int snprintf(char *str, size_t size, const char *format, ...);

/* File operations (stubs for CP/M - limited functionality) */
FILE *fopen(const char *pathname, const char *mode);
int fclose(FILE *stream);
size_t fread(void *ptr, size_t size, size_t nmemb, FILE *stream);
size_t fwrite(const void *ptr, size_t size, size_t nmemb, FILE *stream);
int fgetc(FILE *stream);
int fputc(int c, FILE *stream);
/* libc fgets reads `size` as 16 bits.  Declare it as unsigned short so
 * --int=32 callers don't widen the arg to 4 bytes and shove the stream
 * pointer past the offset libc expects. */
char *fgets(char *s, unsigned short size, FILE *stream);
int fputs(const char *s, FILE *stream);
int feof(FILE *stream);
int ferror(FILE *stream);
void clearerr(FILE *stream);
int ungetc(int c, FILE *stream);
int fflush(FILE *stream);
int fseek(FILE *stream, long offset, int whence);
long ftell(FILE *stream);
void rewind(FILE *stream);

/* Seek constants */
#define SEEK_SET 0
#define SEEK_CUR 1
#define SEEK_END 2

/* Buffer modes */
#define _IOFBF 0
#define _IOLBF 1
#define _IONBF 2

/* Buffer size */
#define BUFSIZ 128

/* Maximum file name length */
#define FILENAME_MAX 12  /* CP/M 8.3 format */

/* Maximum open files */
#define FOPEN_MAX 8

/* Maximum temporary file name length */
#define L_tmpnam 13      /* TMP00000.$$$ + null */

/* Temporary file count limit */
#define TMP_MAX 32767

/* File position type */
typedef long fpos_t;

/* File operations - additional ANSI C functions */
int remove(const char *pathname);
int rename(const char *oldpath, const char *newpath);
FILE *tmpfile(void);
char *tmpnam(char *s);
FILE *freopen(const char *pathname, const char *mode, FILE *stream);

/* Character I/O (can be macros, but implemented as functions for safety) */
int getc(FILE *stream);
int putc(int c, FILE *stream);

/* File position using fpos_t */
int fgetpos(FILE *stream, fpos_t *pos);
int fsetpos(FILE *stream, const fpos_t *pos);

/* Buffering control */
void setbuf(FILE *stream, char *buf);
int setvbuf(FILE *stream, char *buf, int mode, size_t size);

/* Error reporting */
void perror(const char *s);

/* Variadic printf family (requires stdarg.h) */
int vprintf(const char *format, ...);
int vfprintf(FILE *stream, const char *format, ...);
int vsprintf(char *str, const char *format, ...);

/* Variadic scanf family (requires stdarg.h) */
int scanf(const char *format, ...);
int fscanf(FILE *stream, const char *format, ...);
int sscanf(const char *str, const char *format, ...);

#endif /* _STDIO_H */
