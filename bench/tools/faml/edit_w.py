import re
def edit(kind, t):
    t = t.replace("call\t_qsort", "call\t__qsort_w").replace("call\t__qsort_s", "call\t__qsort_w")
    t = re.sub(r"\tEXTRN\t_qsort\n", "", t)
    t = re.sub(r"\tEXTRN\t__qsort_s\n", "", t)
    if "EXTRN\t__qsort_w" not in t:
        t = t.replace("PUBLIC\t_main", "EXTRN\t__qsort_w\n\tPUBLIC\t_main",1)
    return t
