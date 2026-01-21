import logging

import pikepdf

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def mutate(instructions, context):
    new_instructions = []
    # Get stroke width from CLI args, default to 1.0
    try:
        stroke_width = float(context["args"][0]) if context["args"] else 1.0
    except (ValueError, IndexError):
        stroke_width = 1.0

    logger.debug("stroke_width=%s", stroke_width)

    # Inject default width
    new_instructions.append(([stroke_width], pikepdf.Operator("w")))

    for operands, operator in instructions:
        if operator in (pikepdf.Operator("Tj"), pikepdf.Operator("TJ")):
            # Set Text Rendering Mode to 2 (Fill + Stroke)
            new_instructions.append(([stroke_width], pikepdf.Operator("w")))
            new_instructions.append(([2], pikepdf.Operator("Tr")))
            new_instructions.append((operands, operator))
        else:
            new_instructions.append((operands, operator))

    return new_instructions
