#!/bin/bash
# qrun.sh TAG EDIT OBJ "DEFS" prog...
tag=$1; edit=$2; obj=$3; defs=$4; shift 4
od=(); for d in $defs; do od+=(--odef $d); done
cd /private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/wt/famlan
export PYTHONPATH=$PWD/src:$PWD/../bugfix6core/src
for p in "$@"; do python3 ../../faml_an/ab.py $p --tag $tag --edit $edit --obj $obj "${od[@]}" 2>&1 | grep -v '^$'; done
