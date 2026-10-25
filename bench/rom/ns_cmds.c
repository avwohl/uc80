/* C sound commands for nullsound (docs/rom_interop.md).
 *
 * nullsound owns the vectors, the start-up, the stack and the NMI.  Its
 * main loop calls cmd_jmptable[command] (bench/rom/ns_user.s), and two
 * entries jump to the functions below.  Build:
 *
 *   uc80 --target=rom --asm-syntax=sdas --sdas-style=ngdevkit \
 *        --no-embed-startup --entry cmd_init --entry cmd_key_on --entry cmd_key_off \
 *        ns_cmds.c -o ns_cmds.s
 *
 * Rules for this file: no initialised global (nothing copies the initial
 * data), no float, no 64-bit arithmetic, no call that needs the alternate
 * registers.  A global is in DATA and nothing clears DATA.  Command 02h
 * (nullsound's "eye catcher" hook, which runs once after the driver
 * start) sets every variable of this file.
 */
#include <z80.h>

#define PORT_TO_68K 0x0C

/* asm shim in ns_user.s: ym2610_write_port_a (b = register, c = data) */
void ns_ym_a(unsigned char reg, unsigned char val);

/* YM2610 F-number for the 12 notes of one octave (block 4, 8 MHz clock). */
static const unsigned short fnum[12] = {
    0x26A, 0x28F, 0x2B6, 0x2DF, 0x30B, 0x339,
    0x36A, 0x39E, 0x3D5, 0x410, 0x44E, 0x48F
};

static unsigned char key_count;    /* number of key-on commands */
static unsigned char last_note;

/* Command 02h: nullsound runs this after the driver start (the hook of
 * snd_command_02_eye_catcher_music).  The code sets the variables. */
void cmd_init(void)
{
    key_count = 0;
    last_note = 0;
    z80_out(PORT_TO_68K, 0x20);
}

/* Command 04h: play the next note on FM channel 1 (all four operators). */
void cmd_key_on(void)
{
    unsigned char note;
    unsigned short f;

    note = (unsigned char)(key_count % 12);   /* runtime helper __mod16 */
    f = fnum[note];
    ns_ym_a(0xA4, (unsigned char)((4 << 3) | (f >> 8)));  /* block, F-num high */
    ns_ym_a(0xA0, (unsigned char)(f & 0xFF));             /* F-num low */
    ns_ym_a(0x28, 0xF1);                                  /* key on, channel 1 */
    last_note = note;
    key_count++;
    z80_out(PORT_TO_68K, 0x40 | (key_count & 0x3F));      /* reply to the 68k */
}

/* Command 05h: key off FM channel 1. */
void cmd_key_off(void)
{
    ns_ym_a(0x28, 0x01);
    z80_out(PORT_TO_68K, 0x80 | last_note);
}
