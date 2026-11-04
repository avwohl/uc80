#!/bin/bash
. /private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/famm_an/env.sh
cd $W/bench/tools/famm
python3 combo.py $A/p_pi1.py $A/p_while1.py $A/p_pimul.py
python3 combo.py $A/p_tak1.py $A/p_putstr.py
case $1 in
 a) python3 ab.py crc --tag fin --edit $A/p_crcc.py
    python3 ab.py davidly-e --tag fin --edit $A/p_dav1.py
    AB_SRC_EDIT=$A/s_enum8.py python3 ab.py dhrystone21 --tag fin
    AB_SRC_EDIT=$A/s_enum8.py python3 ab.py dhrystone21+nostructassign --tag fin ;;
 b) AB_SRC_EDIT=$A/s_enum8.py python3 ab.py dhrystone21+static --tag fin --edit $A/p_dhall2.py
    python3 ab.py pi+static --tag fin --edit $A/p_pi1.py
    AB_SRC_EDIT=$A/s_dm.py python3 ab.py queens --tag fin --edit $A/p_qall.py
    AB_SRC_EDIT=$A/s_dm.py python3 ab.py queens --tag fin2 --edit $A/p_qall2.py ;;
 c) python3 ab.py sort-ran-20 --tag fin --edit $A/p_sortall.py
    python3 ab.py sort-ran-5000 --tag fin --edit $A/p_sortall.py
    python3 ab.py stanford-bubble --tag fin --edit $A/p_bub1.py
    AB_SRC_EDIT=$A/s_dm.py python3 ab.py tak --tag fin --edit $A/p_tak1.py ;;
esac
