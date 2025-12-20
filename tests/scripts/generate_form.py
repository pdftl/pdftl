from reportlab.lib.colors import black
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# PDF Standard Flags (ISO 32000-1)
FF_Multiline = 1 << 12  # 4096
FF_NoToggleToOff = 1 << 14  # 16384
FF_Radio = 1 << 15  # 32768
FF_Pushbutton = 1 << 16  # 65536
FF_Combo = 1 << 17  # 131072
FF_Edit = 1 << 18  # 262144
FF_MultiSelect = 1 << 21  # 2097152
FF_Comb = 1 << 24  # 16777216


def generate():
    c = canvas.Canvas("tests/assets/Form.pdf", pagesize=letter)
    c.setFont("Helvetica", 12)
    form = c.acroForm

    c.drawString(50, 750, "Synthetically Generated Form.pdf")

    # 1. Text: FullName (Simple)
    c.drawString(50, 700, "Full Name:")
    form.textfield(name="FullName", x=150, y=690, width=200, height=20, textColor=black)

    # 2. Text: ID (Comb flag = 16777216)
    # Note: maxLen is required for Comb to work visually in Acrobat,
    # but the flag is what your parser checks.
    c.drawString(50, 660, "ID (Comb):")
    form.textfield(name="ID", x=150, y=650, width=200, height=20, fieldFlags=FF_Comb, maxlen=10)

    # 3. Checkbox: Married
    c.drawString(50, 620, "Married:")
    form.checkbox(
        name="Married",
        x=150,
        y=620,
        buttonStyle="check",
        borderColor=black,
        fillColor=black,
    )

    # 4. Choice: City (Combo + Edit = 393216)
    c.drawString(50, 580, "City (Combo):")
    cities = ["New York", "London", "Berlin", "Paris", "Rome"]
    form.choice(
        name="City",
        x=150,
        y=570,
        width=100,
        height=20,
        options=cities,
        fieldFlags=FF_Combo | FF_Edit,
        value="New York",
    )

    # 5. Choice: Language (MultiSelect = 2097152)
    c.drawString(50, 530, "Language (Multi):")
    langs = ["English", "German", "French", "Italian"]
    form.listbox(
        name="Language",
        x=150,
        y=480,
        width=100,
        height=50,
        options=langs,
        fieldFlags=FF_MultiSelect,
        value="English",
    )

    # 6. Text: Notes (Multiline = 4096)
    c.drawString(50, 440, "Notes:")
    form.textfield(name="Notes", x=150, y=380, width=200, height=50, fieldFlags=FF_Multiline)

    # 7. Button: ResetButton (Pushbutton = 65536)
    c.drawString(50, 340, "Reset:")
    form.checkbox(
        name="ResetButton",
        x=150,
        y=340,
        size=20,
        buttonStyle="circle",
        fieldFlags=FF_Pushbutton,
    )

    # 8. Radio: Gender (Radio + NoToggleToOff = 49152)
    # ReportLab handles the grouping if names are identical
    c.drawString(50, 300, "Gender:")
    form.radio(
        name="Gender",
        x=150,
        y=300,
        value="Male",
        selected=True,
        fieldFlags=FF_Radio | FF_NoToggleToOff,
    )
    c.drawString(170, 300, "Male")

    form.radio(
        name="Gender",
        x=220,
        y=300,
        value="Female",
        selected=False,
        fieldFlags=FF_Radio | FF_NoToggleToOff,
    )
    c.drawString(240, 300, "Female")

    c.save()
    # print("Generated tests/assets/Form.pdf")


if __name__ == "__main__":
    generate()
