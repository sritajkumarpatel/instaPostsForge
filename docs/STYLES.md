# Post styles: what each one looks like

These are rough examples drawn by the real layout code (`src/instapostsforge/imaging/layouts.py`). The sky photo is from the account; the other "photos" are simple placeholder scenes, so judge the layout, not the pictures. Every slide is 1080×1350 (4:5). How to choose a style is in the README, under [Post styles](../README.md#post-styles).

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

## Auto

Code picks one of the styles above that suits the day's photos and their mood: a poster or gallery frame for calm, nostalgic or romantic photos, a panorama for a wide photo of awe, a grid or story for a set. It never picks the same look two days running. You can limit its choices with `selection.style.auto_styles`.
