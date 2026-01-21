def mutate(instructions, context):
    """
    Converts color instructions (RGB/CMYK) to grayscale.

    context['is_xobject']: True if we are inside a Form XObject
    context['page_num']: Current page being processed
                         (or None in an XObject as they are document global)
    """

    new_instructions = []

    for operands, operator in instructions:
        op_str = str(operator).lower()

        # 'rg' (fill) or 'rg' (stroke) for RGB
        # 'k' (fill) or 'k' (stroke) for CMYK
        if op_str in ("rg", "k"):
            gray_operands, gray_operator = _to_gray(operands, operator)
            if gray_operands is not None:
                new_instructions.append((gray_operands, gray_operator))
            else:
                new_instructions.append((operands, operator))
        else:
            new_instructions.append((operands, operator))

    return new_instructions


def _to_gray(nums, operator):
    op_str = str(operator)
    op_lower = op_str.lower()

    # Validation
    if op_lower == "rg" and len(nums) != 3:
        return None, None
    if op_lower == "k" and len(nums) != 4:
        return None, None

    # Convert to RGB first if CMYK
    if op_lower == "k":
        c, y, m, k = map(float, nums)
        r = 1.0 - min(1.0, c + k)
        g = 1.0 - min(1.0, m + k)
        b = 1.0 - min(1.0, y + k)
    else:
        r, g, b = map(float, nums)

    # Standard luminosity formula for grayscale
    gray_value = 0.3 * r + 0.59 * g + 0.11 * b

    # PDF Operator: 'g' is fill gray, 'G' is stroke gray
    # If the original was uppercase (RG, K), the output should be uppercase (G)
    gray_operator = "g" if op_str.islower() else "G"

    return [gray_value], gray_operator
