# Post styles: what each one looks like

These are rough examples drawn by the real layout code (`src/instapostsforge/imaging/layouts.py`). The sky photo is from the account; the other "photos" are simple placeholder scenes, so judge the layout, not the pictures. Every slide is 1080×1350 (4:5). By default the AI picks a style for each post. How to choose one yourself is in the README, under [Post styles](../README.md#post-styles).

The words on each image come from the caption writer and go through the same checks as the caption: the blocked-terms list, the length limits and the reviewer.

## Poster

The photo as a large print on paper, with a title in spaced capitals, a quiet line under it and a handwritten note. **No two posters need look alike:** the layout suits the photo's shape, and each post gets its own combination of layout, finish (plain, white border, taped corners, a slight tilt) and paper tone (cream, oat, sage, mist blue, blush). A re-run on the same day gives the same look, so a preview matches what posts.

| Wide photo: words under it | Wide photo: words above it | Wide photo: edge to edge |
| --- | --- | --- |
| ![Poster, wide photo on top](assets/styles/poster-wide-top.jpg) | ![Poster, words above a taped wide photo](assets/styles/poster-wide-low.jpg) | ![Poster, photo spanning the page](assets/styles/poster-wide-band.jpg) |

| Tall photo on the left | Tall photo on the right | Square photo |
| --- | --- | --- |
| ![Poster, tall photo left, note in the margin](assets/styles/poster-tall-left.jpg) | ![Poster, tall photo right with border and tape](assets/styles/poster-tall-right.jpg) | ![Poster, square photo with border](assets/styles/poster-square-left.jpg) |

Layouts by shape: wide photos (`wide_top`, `wide_low`, `wide_band`), tall photos (`tall_left`, `tall_right`, `tall_centre`) and square ones (`square_top`, `square_left`). Your own decoration PNGs in `config/decor/` (a leafy branch, a sprig) are added at the top left.

## Gallery frame

The photo on an off-white margin with a small museum-style label: the title the AI writes, then the place and month, which come from the photo's notes and never from a guess.

![Gallery frame with label](assets/styles/gallery.jpg)

## Grid

2 to 9 photos from one outing or place, tiled on the first slide with a short line across the middle. Each photo then follows on its own slide, on a gallery margin. The grid sizes are 9 (3×3), 6 (2×3), 4 (2×2), 3 (one tall and two stacked) and 2 (side by side).

| 9 photos | 4 photos | 3 photos, no title | Each photo on its own slide |
| --- | --- | --- | --- |
| ![3x3 grid with title](assets/styles/grid-9.jpg) | ![2x2 grid with title](assets/styles/grid-4.jpg) | ![One tall and two stacked](assets/styles/grid-3.jpg) | ![A single slide after the grid](assets/styles/grid-single-slide.jpg) |

## Story

A short story told in slides: the cover photo edge to edge with a title, the other photos on gallery margins, and a closing line on paper.

| Cover | Closing slide |
| --- | --- |
| ![Story cover with title](assets/styles/story-cover.jpg) | ![Closing line on paper](assets/styles/story-closing.jpg) |

## Panorama

A wide photo (3:2 or wider) cut into 2 or 3 slides that join up as you swipe. No text.

![Panorama in three slides](assets/styles/panorama.jpg)

## Notes

The photo exactly as shot, then the same photo with a hand-drawn layer on top. The analyst names up to three things in the photo and where they are, and the caption writer writes a short note for each. Code places every mark: never inside the subject, never on a person, and never on a photo of a deity, shrine or prayer.

| Slide 1: as shot | Slide 2: field notes | Star map |
| --- | --- | --- |
| ![The photo as shot](assets/styles/notes-clean.jpg) | ![Handwritten notes on the photo](assets/styles/notes-field.jpg) | ![A star map in a night sky](assets/styles/notes-constellation.jpg) |

| Film date stamp | Doodles | Magazine cover |
| --- | --- | --- |
| ![Orange film date stamp](assets/styles/notes-date-stamp.jpg) | ![White doodles in the sky](assets/styles/notes-doodles.jpg) | ![Magazine cover masthead](assets/styles/notes-cover.jpg) |

| Viewfinder | Photographer's notes | Tape label |
| --- | --- | --- |
| ![Viewfinder corners and time](assets/styles/notes-viewfinder.jpg) | ![Rule-of-thirds lines and a note](assets/styles/notes-thirds.jpg) | ![Tape with the place name](assets/styles/notes-tape.jpg) |

### Sketch marks

Quiet pen marks taken from how photographers and illustrators doodle on prints: one colour, a thin wavering line, drawn in the empty space around the subject so the photo still reads first. A look carries one of them, sometimes with the date stamp or a single handwritten word.

- **Ring:** a loose loop around a lone subject (a moon, a lamp, a flower, an animal), left open like a pen stroke, with a small dot riding on it.
- **Rays:** a few short strokes off a bright subject, like a sketch of its glow.
- **Birds:** two or three gulls in the calmest part of an open sky.
- **Aside:** one handwritten word or two with a loose underline, near the subject (the caption writer writes it, at most 24 characters).

| Ring and date stamp | Rays and an aside | Birds |
| --- | --- | --- |
| ![A pen ring around the moon](assets/styles/notes-orbit.jpg) | ![Short rays around the sun and a handwritten word](assets/styles/notes-rays.jpg) | ![Three gulls in the sky](assets/styles/notes-birds.jpg) |

A ring or rays need a lone subject with room around it, and none of the four is drawn on a photo with people. Doodles are kept to two or three small marks. The second slide always carries more than a date stamp: when a look's marks can't be placed (a star map in a sky with no stars, say), code adds a ring or rays around the subject, or corner brackets, so the post never looks like the same photo twice.

## Pop-out

An editorial poster on light paper. The photo shows through a window, and its main subject steps out of the window onto the page: down onto a faded patch of its own ground (a person walking, a jeep), out of one side, or across the top and bottom of a narrow window. A large serif title sits above it with a small spaced label, a spaced line and up to four words along the foot. The title takes a deep colour from the photo when it reads on the paper.

The subject is found by a local segmentation model and the photo's own pixels are shown through that mask, never repainted. The alt text, and by default a short line after the caption, say the layout is a composite of the unedited photo. When the photo has no single clear subject, the subject touches the photo's edges, its edges are unsure (fog, glass, motion blur), or it's a devotional photo or has children, the post is a plain poster instead. Try one of your photos with `forge cutout -p main --photo <name>`, then `forge preview -p main --style popout`.

## Looks: backgrounds and frames

On top of the style, code picks a look by rule, with no AI call: the photo's mood and category choose the candidates, the date picks one, and the last post's look is skipped. Narrow them in `selection.style.looks`.

Backgrounds are drawn from the photo's own colours or a plain paper texture. Text is checked for contrast, and flag or party colour sets never appear together.

| Blurred echo | Two tones | Colour swatches |
| --- | --- | --- |
| ![Poster on a blurred copy of the photo](assets/styles/look-echo.jpg) | ![Poster on two tones](assets/styles/look-two-tone.jpg) | ![Gallery with colour swatches](assets/styles/look-swatches.jpg) |

| Sun shape | Kraft paper | Night navy |
| --- | --- | --- |
| ![Poster with a sun shape](assets/styles/look-sun.jpg) | ![Poster on kraft paper](assets/styles/look-kraft.jpg) | ![Gallery on navy](assets/styles/look-navy.jpg) |

Frames suit the kind of photo: a postcard or ticket for travel and landscapes, film for street and night, a magazine page for city and abstract photos, instant prints for a set of two to four, and a festive border for devotional photos. The place and month come from code, and no frame copies a real brand, ticket or stamp.

| Postcard | Ticket stub | Film strip |
| --- | --- | --- |
| ![Postcard](assets/styles/frame-postcard.jpg) | ![Ticket stub](assets/styles/frame-ticket.jpg) | ![Film strip](assets/styles/frame-film.jpg) |

| Magazine page | Instant prints | Festive border |
| --- | --- | --- |
| ![Magazine page](assets/styles/frame-magazine.jpg) | ![Instant prints](assets/styles/frame-polaroids.jpg) | ![Festive border](assets/styles/frame-festive.jpg) |

## Any (the default)

The AI planner picks one of the styles above for each post from what the analyst saw in the photos: a panorama for a wide landscape, a poster for a quiet or nostalgic photo, a gallery frame for a strong clean one, a grid or story for a set. It avoids the last post's look. If its pick can't be drawn (a panorama of a photo that isn't wide), code picks one that suits the photos' mood. Limit its choices with `selection.style.any_styles`; plain `single` posts happen only when you choose them, or add `single` to that list.
