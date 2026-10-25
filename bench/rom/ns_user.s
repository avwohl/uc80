;;; The user part of a nullsound sound ROM (docs/rom_interop.md).
;;;
;;; nullsound calls cmd_jmptable[command] with a CALL-like push of its own
;;; return address.  Entries 0 to 3 are the BIOS commands.  Entry 2 is also
;;; the hook that nullsound calls once after the driver start.  Entries 2, 4
;;; and 5 jump to C functions that uc80 compiled (ns_cmds.c).  The remaining
;;; entries are filled by nullsound's own macro.
;;;
;;; Register contract at an entry: nullsound has pushed HL, so the entry
;;; may change every register, except that the alternate set (AF', BC',
;;; DE', HL') belongs to the NMI handler.  A uc80 function keeps IX and
;;; does not use IY or the alternate registers, so "jp _cmd_key_on" needs
;;; no wrapper.

        .module ns_user
        .include "helpers.inc"

        .area   CODE

cmd_jmptable::
        jp      snd_command_unused                       ; 00
        jp      snd_command_01_prepare_for_rom_switch    ; 01
        jp      _cmd_init                                ; 02 (C): the hook
        jp      snd_command_03_reset_driver              ; 03
        jp      _cmd_key_on                              ; 04 (C)
        jp      _cmd_key_off                             ; 05 (C)
        init_unused_cmd_jmptable

;;; void ns_ym_a(unsigned char reg, unsigned char val)
;;;
;;; C calls nullsound.  uc80 passes the arguments in stack slots of two
;;; bytes, first argument nearest to the return address:
;;;   (sp+0) return, (sp+2) reg, (sp+4) val.
;;; ym2610_write_port_a takes b = register, c = data and keeps all
;;; registers, so the tail jump returns to the C caller.
_ns_ym_a::
        ld      hl, #2
        add     hl, sp
        ld      b, (hl)
        inc     hl
        inc     hl
        ld      c, (hl)
        jp      ym2610_write_port_a
