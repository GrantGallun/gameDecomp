from measure import OUT, run


def candidates():
    for name, parent in [("Fvibup", 25), ("Fvibdown", 28)]:
        source = (OUT / f"{name}--{parent}.c").read_text()
        for typ in ("u8", "s32", "u32"):
            code = source.replace("u32 temp_t8;", typ + " temp_t8;").replace("(f32) temp_t8;", "(f32) (u32) temp_t8;")
            yield name, "cast-unsigned-" + typ, code, parent
            inline = code.replace("    var_f6 = (f32) (u32) temp_t8;\n", "")
            inline = inline.replace("(f64) var_f6", "(f64) (f32) (u32) temp_t8")
            inline = inline.replace("(f64) -var_f6", "(f64) -(f32) (u32) temp_t8")
            yield name, "cast-unsigned-inline-" + typ, inline, parent


if __name__ == "__main__":
    run("probe3", candidates())
