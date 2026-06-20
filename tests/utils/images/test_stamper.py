# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/images/test_stamper.py

from __future__ import annotations

import pytest
import pikepdf
from PIL import Image

from pdftl.utils.images.stamper import stamp_images_on_pdf


def get_page_contents(page: pikepdf.Page) -> bytes:
    """Helper to safely retrieve decoded (uncompressed) page contents."""
    if "/Contents" not in page:
        return b""
    contents = page.Contents
    if isinstance(contents, pikepdf.Array):
        return b"".join(bytes(s) for s in contents)
    return bytes(contents)


@pytest.fixture
def sample_pdf_path(tmp_path):
    """Creates a simple 2-page PDF document with standard MediaBox (600 width, 800 height)."""
    pdf_path = tmp_path / "sample.pdf"
    with pikepdf.Pdf.new() as pdf:
        pdf.add_blank_page(page_size=(600, 800))
        pdf.add_blank_page(page_size=(600, 800))
        pdf.save(pdf_path)
    return pdf_path


@pytest.fixture
def sample_3page_pdf_path(tmp_path):
    """Creates a simple 3-page PDF document with standard MediaBox."""
    pdf_path = tmp_path / "sample_3p.pdf"
    with pikepdf.Pdf.new() as pdf:
        pdf.add_blank_page(page_size=(600, 800))
        pdf.add_blank_page(page_size=(600, 800))
        pdf.add_blank_page(page_size=(600, 800))
        pdf.save(pdf_path)
    return pdf_path


@pytest.fixture
def sample_image_path(tmp_path):
    """Creates a small RGB test PNG."""
    img_path = tmp_path / "stamp.png"
    img = Image.new("RGB", (100, 50), color="blue")
    img.save(img_path, format="PNG")
    return img_path


def test_stamp_images_overlay(sample_pdf_path, sample_image_path, tmp_path):
    """Tests basic image overlay on all pages."""
    output_pdf_path = tmp_path / "output.pdf"

    with pikepdf.open(sample_pdf_path) as pdf:
        stamp_images_on_pdf(
            pdf=pdf,
            images=[sample_image_path],
            pages="1-end",
            underlay=False,
            width="50pt",
            height="25pt",
        )
        pdf.save(output_pdf_path)

    with pikepdf.open(output_pdf_path) as pdf:
        assert len(pdf.pages) == 2
        for page in pdf.pages:
            # Confirm unique image registered
            assert "/XObject" in page.Resources
            xobjects = page.Resources.XObject
            assert len(xobjects.keys()) == 1

            # Check that content contains drawing commands
            contents = get_page_contents(page)
            assert b"cm" in contents
            assert b"Do" in contents


def test_stamp_images_no_contents_overlay(sample_pdf_path, sample_image_path, tmp_path):
    """Verifies that overlaying images on a page with absolutely no /Contents key works seamlessly."""
    output_pdf_path = tmp_path / "output_no_contents.pdf"

    with pikepdf.open(sample_pdf_path) as pdf:
        # Explicitly delete the /Contents key on page 1
        del pdf.pages[0].Contents

        stamp_images_on_pdf(
            pdf=pdf,
            images=[sample_image_path],
            pages="1",
            underlay=False,
            width="50pt",
        )
        pdf.save(output_pdf_path)

    with pikepdf.open(output_pdf_path) as pdf:
        page = pdf.pages[0]
        # Confirm /Contents key was generated successfully
        assert "/Contents" in page
        contents = get_page_contents(page)
        assert b"Do" in contents


def test_stamp_images_underlay(sample_pdf_path, sample_image_path, tmp_path):
    """Tests placing the image as an underlay."""
    output_pdf_path = tmp_path / "output_underlay.pdf"

    with pikepdf.open(sample_pdf_path) as pdf:
        # Pre-seed a dummy content stream
        pdf.pages[0].Contents = pdf.make_stream(b"0 0 0 rg BT /F1 12 Tf (TEXT) Tj ET\n")

        stamp_images_on_pdf(
            pdf=pdf,
            images=[sample_image_path],
            pages="1",
            underlay=True,
            width="50pt",
        )
        pdf.save(output_pdf_path)

    with pikepdf.open(output_pdf_path) as pdf:
        page = pdf.pages[0]
        assert isinstance(page.Contents, pikepdf.Array)
        # First stream must be our newly injected underlay image stream
        assert b"Do" in bytes(page.Contents[0])
        # Second stream is the original text stream
        assert b"TEXT" in bytes(page.Contents[1])


def test_stamp_images_existing_array_underlay(sample_pdf_path, sample_image_path, tmp_path):
    """Verifies underlay injection when Contents is already a pikepdf.Array."""
    output_pdf_path = tmp_path / "output_array_underlay.pdf"

    with pikepdf.open(sample_pdf_path) as pdf:
        # Pre-seed with two streams in an Array
        stream1 = pdf.make_stream(b"BT /F1 12 Tf (STREAM_ONE) Tj ET\n")
        stream2 = pdf.make_stream(b"BT /F2 12 Tf (STREAM_TWO) Tj ET\n")
        pdf.pages[0].Contents = pikepdf.Array([stream1, stream2])

        stamp_images_on_pdf(
            pdf=pdf,
            images=[sample_image_path],
            pages="1",
            underlay=True,
            width="50pt",
        )
        pdf.save(output_pdf_path)

    with pikepdf.open(output_pdf_path) as pdf:
        page = pdf.pages[0]
        assert isinstance(page.Contents, pikepdf.Array)
        assert len(page.Contents) == 3
        # Newly injected image stream is first
        assert b"Do" in bytes(page.Contents[0])
        # Original streams are preserved downstream
        assert b"STREAM_ONE" in bytes(page.Contents[1])
        assert b"STREAM_TWO" in bytes(page.Contents[2])


def test_stamp_images_existing_array_overlay(sample_pdf_path, sample_image_path, tmp_path):
    """Verifies overlay injection when Contents is already a pikepdf.Array."""
    output_pdf_path = tmp_path / "output_array_overlay.pdf"

    with pikepdf.open(sample_pdf_path) as pdf:
        # Pre-seed with two streams in an Array
        stream1 = pdf.make_stream(b"BT /F1 12 Tf (STREAM_ONE) Tj ET\n")
        stream2 = pdf.make_stream(b"BT /F2 12 Tf (STREAM_TWO) Tj ET\n")
        pdf.pages[0].Contents = pikepdf.Array([stream1, stream2])

        stamp_images_on_pdf(
            pdf=pdf,
            images=[sample_image_path],
            pages="1",
            width="50pt",
        )
        pdf.save(output_pdf_path)

    with pikepdf.open(output_pdf_path) as pdf:
        page = pdf.pages[0]
        assert isinstance(page.Contents, pikepdf.Array)
        assert len(page.Contents) == 3
        # Original streams remain first
        assert b"STREAM_ONE" in bytes(page.Contents[0])
        assert b"STREAM_TWO" in bytes(page.Contents[1])
        # Newly injected image stream is appended last
        assert b"Do" in bytes(page.Contents[2])


def test_stamp_images_opacity(sample_pdf_path, sample_image_path, tmp_path):
    """Verifies that setting opacity properly injects ExtGState resources."""
    output_pdf_path = tmp_path / "output_opacity.pdf"

    with pikepdf.open(sample_pdf_path) as pdf:
        stamp_images_on_pdf(
            pdf=pdf,
            images=[sample_image_path],
            pages="1",
            opacity=0.5,
        )
        pdf.save(output_pdf_path)

    with pikepdf.open(output_pdf_path) as pdf:
        page = pdf.pages[0]
        assert "/ExtGState" in page.Resources
        ext_gstates = page.Resources.ExtGState
        assert len(ext_gstates.keys()) == 1

        # Verify alpha values
        gs = ext_gstates[list(ext_gstates.keys())[0]]
        assert float(gs.ca) == 0.5
        assert float(gs.CA) == 0.5


def test_stamp_images_rotation(sample_3page_pdf_path, sample_image_path, tmp_path):
    """Verifies that pages with visual display rotations (90, 180, 270) translate coordinates correctly."""
    output_pdf_path = tmp_path / "output_rotation.pdf"

    with pikepdf.open(sample_3page_pdf_path) as pdf:
        # Pre-rotate page 1 to 90 degrees, page 2 to 180 degrees, and page 3 to 270 degrees
        pdf.pages[0].Rotate = 90
        pdf.pages[1].Rotate = 180
        pdf.pages[2].Rotate = 270

        stamp_images_on_pdf(
            pdf=pdf,
            images=[sample_image_path],
            pages="1-end",
            underlay=False,
            position="bottom-left",
            width="100pt",
            height="50pt",
        )
        pdf.save(output_pdf_path)

    with pikepdf.open(output_pdf_path) as pdf:
        # 1. Check 90 degrees visual transformation
        page_90 = pdf.pages[0]
        contents_90 = get_page_contents(page_90)
        # Visual bottom-left translates to native bottom-right:
        # Expected native scale components: b=100.0, c=-50.0. Translates to native bottom-right: e=600.0, f=0.0.
        assert b"0.0000 100.0000 -50.0000 0.0000 600.0000 0.0000 cm" in contents_90

        # 2. Check 180 degrees visual transformation
        page_180 = pdf.pages[1]
        contents_180 = get_page_contents(page_180)
        # Visual bottom-left translates to native top-right:
        # Expected native scale components: a=-100.0, d=-50.0. Translates to native top-right: e=600.0, f=800.0.
        assert b"-100.0000 0.0000 0.0000 -50.0000 600.0000 800.0000 cm" in contents_180

        # 3. Check 270 degrees visual transformation
        page_270 = pdf.pages[2]
        contents_270 = get_page_contents(page_270)
        # Visual bottom-left translates to native top-left:
        # Expected native cross components: b=-100.0, c=50.0. Translates to native top-left: e=0.0, f=800.0.
        assert b"0.0000 -100.0000 50.0000 0.0000 0.0000 800.0000 cm" in contents_270


def test_stamp_images_percentage_sizing(sample_pdf_path, sample_image_path, tmp_path):
    """Verifies that percentage values (e.g. '100%' width, '50%' height) scale perfectly relative to page size."""
    output_pdf_path = tmp_path / "output_percentage.pdf"

    with pikepdf.open(sample_pdf_path) as pdf:
        # Page size is (600, 800).
        # We specify 100% width (should resolve to 600 points) and 50% height (should resolve to 400 points)
        stamp_images_on_pdf(
            pdf=pdf,
            images=[sample_image_path],
            pages="1",
            width="100%",
            height="50%",
        )
        pdf.save(output_pdf_path)

    with pikepdf.open(output_pdf_path) as pdf:
        page = pdf.pages[0]
        contents = get_page_contents(page)
        # Confirm that the drawn matrix scale factors match exactly: a=600.0, d=400.0
        assert b"600.0000 0.0000 0.0000 400.0000" in contents


def test_stamp_images_empty_offset(sample_pdf_path, sample_image_path, tmp_path):
    """Verifies that an empty offset string defaults cleanly to (0.0, 0.0)."""
    output_pdf_path = tmp_path / "output_empty_offset.pdf"

    with pikepdf.open(sample_pdf_path) as pdf:
        stamp_images_on_pdf(
            pdf=pdf,
            images=[sample_image_path],
            pages="1",
            offset_x="",  # Check empty offsets
            offset_y="",
            width="50pt",
        )
        pdf.save(output_pdf_path)

    with pikepdf.open(output_pdf_path) as pdf:
        page = pdf.pages[0]
        contents = get_page_contents(page)
        assert b"0.0000 0.0000 cm" in contents


def test_stamp_images_missing_file(sample_pdf_path):
    """Ensures FileNotFoundError is raised cleanly when image file is missing."""
    with pikepdf.open(sample_pdf_path) as pdf:
        with pytest.raises(FileNotFoundError):
            stamp_images_on_pdf(
                pdf=pdf,
                images=["nonexistent_image.png"],
                pages="1-end",
                width="50pt",
            )


def test_stamp_images_no_matching_pages(sample_pdf_path, sample_image_path):
    """Verifies that no changes are made if page spec resolves to nothing."""
    with pikepdf.open(sample_pdf_path) as pdf:
        stamp_images_on_pdf(
            pdf=pdf,
            images=[sample_image_path],
            pages="99",  # Out of bounds
            width="50pt",
        )
        assert "/XObject" not in pdf.pages[0].Resources
