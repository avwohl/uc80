import re
def fix(wd):
    for f in wd.glob("*.c"):
        t = f.read_text()
        if "put_dec" not in t: continue
        t2 = t.replace("v /= 10u;", "v = v / 10u;")
        t2 = re.sub(r"static void put_dec\(uint16_t v\)", "static void put_dec(unsigned v)", t2)
        if t2 != t: f.write_text(t2); print("edited", f.name)
