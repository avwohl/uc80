"""The scanf family: sscanf, vsscanf, fscanf and scanf.

The first program runs 80 sscanf calls.  Its expected output was checked
against the C library of a host compiler; the only change is that an int has
16 bits here.
"""

import pytest

from ._clib import build_and_run, skip_without_tools

pytestmark = skip_without_tools

SSCANF_PROGRAM = r"""
#include <stdio.h>
#include <string.h>
#include <stdint.h>
static unsigned long bits(float d){ uint32_t u; memcpy(&u,&d,4); return (unsigned long)u; }
#define P(...) printf(__VA_ARGS__)
int main(void){
 int r,i,j,k,n; long l; long long q; unsigned u; unsigned short us; short sh; signed char hh; unsigned char uh;
 float f; double d; char s[40], t[40], c[8]; unsigned long ul; unsigned long long uq; void *p;
 /* ints */
 r=sscanf("42 -17 +5","%d %d %d",&i,&j,&k); P("1: %d %d %d %d\n",r,i,j,k);
 r=sscanf("0x1f 017 99 0X10","%i %i %i %i",&i,&j,&k,&n); P("2: %d %d %d %d %d\n",r,i,j,k,n);
 r=sscanf("ff 7fff -a 0xAB","%x %x %x %x",&i,&j,&k,&n); P("3: %d %d %d %d %d\n",r,i,j,k,n);
 r=sscanf("777 18 -10","%o %o %o",&i,&j,&k); P("4: %d %d %d %d\n",r,i,j,k);
 r=sscanf("12345 65535 -1","%u %u %u",&u,&i,&j); P("5: %d %u %u %u\n",r,u,(unsigned)i,(unsigned)j);
 r=sscanf("123456 -7 4000000","%ld %ld %ld",&l,&ul,&q); P("6: %d %ld\n",r,l);
 r=sscanf("123456789 -1234567890123","%ld %lld",&l,&q); P("7: %d %ld %lld\n",r,l,q);
 r=sscanf("300 -5 70000","%hhd %hd %d",&hh,&sh,&i); P("8: %d %d %d\n",r,(int)hh,(int)sh);
 r=sscanf("255 65535","%hhu %hu",&uh,&us); P("9: %d %u %u\n",r,(unsigned)uh,(unsigned)us);
 r=sscanf("18446744073709551615 9223372036854775807","%llu %lld",&uq,&q); P("10: %d %llu %lld\n",r,uq,q);
 r=sscanf("0x7fffffff 0xffffffff","%lx %lx",&l,&ul); P("11: %d %lx %lx\n",r,l,ul);
 r=sscanf("-9223372036854775808","%lld",&q); P("12: %d %lld\n",r,q);
 /* widths */
 r=sscanf("12345","%3d%d",&i,&j); P("13: %d %d %d\n",r,i,j);
 r=sscanf("abcdef","%3s%s",s,t); P("14: %d %s %s\n",r,s,t);
 r=sscanf("abcdef","%3c%c",c,&c[3]); P("15: %d %.3s %c\n",r,c,c[3]);
 r=sscanf("12345","%3c%d",c,&i); P("16: %d %.3s %d\n",r,c,i);
 r=sscanf("-12","%2d%d",&i,&j); P("17: %d %d %d\n",r,i,j);
 r=sscanf("0x1fz","%3x%x",&i,&j); P("18: %d %d %d\n",r,i,j);
 /* suppression */
 r=sscanf("1 2 3","%*d %d %*d",&i); P("19: %d %d\n",r,i);
 r=sscanf("abc 5","%*s %d",&i); P("20: %d %d\n",r,i);
 r=sscanf("abc","%*s%n",&n); P("21: %d %d\n",r,n);
 r=sscanf("x 5","%*c %d",&i); P("21b: %d %d\n",r,i);
 /* %n */
 r=sscanf("abc12 rest","abc%d%n",&i,&n); P("22: %d %d %d\n",r,i,n);
 r=sscanf("  42","%n%d%n",&j,&i,&n); P("23: %d %d %d %d\n",r,j,i,n);
 r=sscanf("1 2","%d%hhn%d",&i,&hh,&j); P("24: %d %d %d %d\n",r,i,(int)hh,j);
 /* scansets */
 r=sscanf("abc123def","%[a-z]%[0-9]%s",s,t,c); P("25: %d %s %s %s\n",r,s,t,c);
 r=sscanf("hello world","%[^ ] %s",s,t); P("26: %d %s %s\n",r,s,t);
 r=sscanf("]]x","%[]]%s",s,t); P("27: %d %s %s\n",r,s,t);
 r=sscanf("--ab","%[-a]",s); P("28: %d %s\n",r,s);
 r=sscanf("abcXYZ","%3[a-zA-Z]%s",s,t); P("29: %d %s %s\n",r,s,t);
 r=sscanf("123","%[a-z]",s); P("30: %d\n",r);
 r=sscanf("ab  cd","%[a-z] %[a-z]",s,t); P("31: %d %s %s\n",r,s,t);
 r=sscanf("a-b","%[^-]-%s",s,t); P("32: %d %s %s\n",r,s,t);
 r=sscanf("   x","%[ x]",s); P("33: %d [%s]\n",r,s);
 /* %% and literals */
 r=sscanf("50%","%d%%",&i); P("34: %d %d\n",r,i);
 r=sscanf("50 % 7","%d %% %d",&i,&j); P("35: %d %d %d\n",r,i,j);
 r=sscanf("a,b","%[a-z],%[a-z]",s,t); P("36: %d %s %s\n",r,s,t);
 r=sscanf("x=5","y=%d",&i); P("37: %d\n",r);
 r=sscanf("x=5","x=%d",&i); P("38: %d %d\n",r,i);
 /* return values / EOF */
 r=sscanf("","%d",&i); P("39: %d\n",r);
 r=sscanf("   ","%d",&i); P("40: %d\n",r);
 r=sscanf("abc","%d",&i); P("41: %d\n",r);
 r=sscanf("5","%d %d",&i,&j); P("42: %d %d\n",r,i);
 r=sscanf("5 x","%d %d",&i,&j); P("43: %d %d\n",r,i);
 r=sscanf("","%s",s); P("44: %d\n",r);
 r=sscanf("","abc"); P("45: %d\n",r);
 r=sscanf("abc","abc"); P("46: %d\n",r);
 r=sscanf("","%c",c); P("47: %d\n",r);
 r=sscanf("","%n",&n); P("48: %d %d\n",r,n);
 r=sscanf("-","%d",&i); P("49: %d\n",r);
 r=sscanf("+x","%d",&i); P("50: %d\n",r);
 r=sscanf("12abc","%d%s",&i,s); P("51: %d %d %s\n",r,i,s);
 r=sscanf("1 2 3 4","%d%d",&i,&j); P("52: %d %d %d\n",r,i,j);
 r=sscanf("a b","%c%c%c",&c[0],&c[1],&c[2]); P("53: %d %c %c %c\n",r,c[0],c[1],c[2]);
 r=sscanf("a b","%c %c",&c[0],&c[1]); P("54: %d %c %c\n",r,c[0],c[1]);
 /* floats */
 r=sscanf("1.5 2.25 -3e2 .5 7.","%f %f %f %f %f",&f,&d,&f,&f,&f); P("55: %d %08lx\n",r,bits(f));
 { float a,b,e2; double dd; r=sscanf("1.5 -2.25e1 3","%f %lf %f",&a,&dd,&b); P("56: %d %08lx %08lx %08lx\n",r,bits(a),bits((float)dd),bits(b));
   r=sscanf("inf -inf nan","%f %f %f",&a,&b,&e2); P("57: %d %08lx %08lx %08lx\n",r,bits(a),bits(b),bits(e2));
   r=sscanf("1e5x","%f%s",&a,s); P("58: %d %08lx %s\n",r,bits(a),s);
   r=sscanf("1e","%f%s",&a,s); P("59: %d %08lx\n",r,bits(a));
   r=sscanf("0x1p3","%f",&a); P("60: %d %08lx\n",r,bits(a));
   r=sscanf("abc","%f",&a); P("61: %d\n",r);
   r=sscanf("","%f",&a); P("62: %d\n",r);
   r=sscanf("3.14159","%3f%f",&a,&b); P("63: %d %08lx %08lx\n",r,bits(a),bits(b));
   r=sscanf("12.5 7","%f%*f%n",&a,&n); P("64: %d %08lx %d\n",r,bits(a),n);
   r=sscanf("1.5e+3","%e",&a); P("65: %d %08lx\n",r,bits(a));
   r=sscanf("-0.0 100","%g %G",&a,&b); P("66: %d %08lx %08lx\n",r,bits(a),bits(b));
   r=sscanf("1.5abc","%f",&a); P("67: %d %08lx\n",r,bits(a));
   r=sscanf("+.5e-1","%f",&a); P("68: %d %08lx\n",r,bits(a));
   r=sscanf("infinity","%f",&a); P("69: %d %08lx\n",r,bits(a));
   r=sscanf("-.","%f",&a); P("70: %d\n",r);
   r=sscanf("1.5 2.5","%Lf %lf",&dd,&d); P("71: %d %08lx\n",r,bits((float)dd));
 }
 /* pointer, mixed */
 r=sscanf("1f 2","%p %d",&p,&i); P("72: %d %d\n",r,(int)((unsigned long)p&0xffff));
 r=sscanf("name: bob age: 33","name: %s age: %d",s,&i); P("73: %d %s %d\n",r,s,i);
 r=sscanf("10:20:30","%d:%d:%d",&i,&j,&k); P("74: %d %d %d %d\n",r,i,j,k);
 r=sscanf("0","%i",&i); P("75: %d %d\n",r,i);
 r=sscanf("08","%i",&i); P("76: %d %d\n",r,i);
 r=sscanf("0xZ","%i",&i); P("77: %d\n",r);
 r=sscanf("  \t\n7","%d",&i); P("78: %d %d\n",r,i);
 r=sscanf("12","%1d%1d",&i,&j); P("79: %d %d %d\n",r,i,j);
 r=sscanf("abc def","%s%n",s,&n); P("80: %d %s %d\n",r,s,n);
 r=sscanf("7 8","%d %n%d",&i,&n,&j); P("81: %d %d %d %d\n",r,i,n,j);
 r=sscanf("x","%c%n",c,&n); P("82: %d %c %d\n",r,c[0],n);
 return 0;}
"""

SSCANF_EXPECTED = ['1: 3 42 -17 5', '2: 4 31 15 99 16', '3: 4 255 32767 -10 171', '4: 2 511 1 -10', '5: 3 12345 65535 65535', '6: 3 123456', '7: 2 123456789 -1234567890123', '8: 3 44 -5', '9: 2 255 65535', '10: 2 18446744073709551615 9223372036854775807', '11: 2 7fffffff ffffffff', '12: 1 -9223372036854775808', '13: 2 123 45', '14: 2 abc def', '15: 2 abc d', '16: 2 123 45', '17: 2 -1 2', '18: 2 1 15', '19: 1 2', '20: 1 5', '21: 0 3', '21b: 1 5', '22: 1 12 5', '23: 1 0 42 4', '24: 2 1 1 2', '25: 3 abc 123 def', '26: 2 hello world', '27: 2 ]] x', '28: 1 --a', '29: 2 abc XYZ', '30: 0', '31: 2 ab cd', '32: 2 a b', '33: 1 [   x]', '34: 1 50', '35: 2 50 7', '36: 2 a b', '37: 0', '38: 1 5', '39: -1', '40: -1', '41: 0', '42: 1 5', '43: 1 5', '44: -1', '45: -1', '46: 0', '47: -1', '48: 0 0', '49: 0', '50: 0', '51: 2 12 abc', '52: 2 1 2', '53: 3 a   b', '54: 2 a b', '55: 5 40e00000', '56: 3 3fc00000 c1b40000 40400000', '57: 3 7f800000 ff800000 7fc00000', '58: 2 47c35000 x', '59: 2 3f800000', '60: 1 41000000', '61: 0', '62: -1', '63: 2 40466666 4581f800', '64: 1 41480000 6', '65: 1 44bb8000', '66: 2 80000000 42c80000', '67: 1 3fc00000', '68: 1 3d4ccccd', '69: 1 7f800000', '70: 0', '71: 2 3fc00000', '72: 2 31', '73: 2 bob 33', '74: 3 10 20 30', '75: 1 0', '76: 1 0', '77: 1', '78: 1 7', '79: 2 1 2', '80: 1 abc 3', '81: 2 7 2 8', '82: 1 x 1']


def test_sscanf_conversions(tmp_path):
    out = build_and_run(tmp_path, SSCANF_PROGRAM)
    assert out.split("\n") == SSCANF_EXPECTED + [""]


CONSOLE_PROGRAM = r"""
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
static int vs(const char *s, const char *f, ...){ va_list ap; int r; va_start(ap,f); r=vsscanf(s,f,ap); va_end(ap); return r; }
int main(void){
 int i,j,r,n; char s[40]; float fl; char c;
 r=scanf("%d %d",&i,&j); printf("a: %d %d %d\n",r,i,j);
 r=scanf("%c",&c); printf("b: %d %d\n",r,(int)c);
 r=scanf(" %s %f",s,&fl); printf("c: %d %s %d\n",r,s,(int)(fl*100));
 r=scanf("%d",&i); printf("d: %d\n",r);
 r=scanf("%d",&i); printf("e: %d\n",r);
 r=vs("5 6","%d %d",&i,&j); printf("f: %d %d %d\n",r,i,j);
 {
  FILE *fp=fopen("T.TXT","w"); fprintf(fp,"10 20\nword 3.5\nxyz"); fclose(fp);
  fp=fopen("T.TXT","r");
  r=fscanf(fp,"%d %d",&i,&j); printf("g: %d %d %d\n",r,i,j);
  r=fscanf(fp,"%s %f",s,&fl); printf("h: %d %s %d\n",r,s,(int)(fl*10));
  r=fscanf(fp,"%s",s); printf("i: %d %s\n",r,s);
  r=fscanf(fp,"%s",s); printf("j: %d\n",r);
  fclose(fp);
  fp=fopen("T.TXT","r"); r=fscanf(fp,"%d%n%c",&i,&n,&c); printf("k: %d %d %d %d\n",r,i,n,(int)c);
  r=fscanf(fp,"%d",&j); printf("l: %d %d\n",r,j); fclose(fp);
 }
 return 0;}
"""


def test_scanf_console_vscanf_fscanf(tmp_path):
    out = build_and_run(tmp_path, CONSOLE_PROGRAM,
                        stdin=b"3 4\nx\nhello 2.5\nabc\n")
    assert out.split("\n") == [
        "a: 2 3 4", "b: 1 13", "c: 1 x 0", "d: 0", "e: 0", "f: 2 5 6",
        "g: 2 10 20", "h: 2 word 35", "i: 1 xyz", "j: -1",
        "k: 2 10 2 32", "l: 1 20", ""]


def test_scanf_end_of_input(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    int i = 7, r;
    r = scanf("%d", &i);
    printf("%d %d\n", r, i);
    r = scanf("%d", &i);
    printf("%d %d\n", r, i);
    return 0;
}
""", stdin=b"")
    assert out.split("\n") == ["-1 7", "-1 7", ""]
