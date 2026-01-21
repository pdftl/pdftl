import logging

import pikepdf

logger = logging.getLogger(__name__)


def mutate(instructions, context):
    new_instructions = []
    count = 0

    # Get threshold from CLI args, default to 0.25
    try:
        min_width = float(context["args"][0]) if context["args"] else 0.25
    except (ValueError, IndexError):
        min_width = 0.25

    for operands, operator in instructions:
        if operator == pikepdf.Operator("w"):
            current_width = float(operands[0])

            if current_width < min_width:
                new_instructions.append(([min_width], operator))
                count += 1
            else:
                new_instructions.append((operands, operator))
        else:
            new_instructions.append((operands, operator))

    if count > 0:
        scope = (
            f"Page {context['page_num']}"
            if context["page_num"]
            else f"XObject {context['xobject_name']}"
        )
        logger.info(f"Fixed {count} hairlines (threshold {min_width}pt) in {scope}")

    return new_instructions
