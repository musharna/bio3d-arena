# Plant Input Advisor — report

## arabidopsis
- growth form: **rosette**
- recon mode: **single**
- capture recipe: top-down (radially flat — natural for a rosette); plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: single-image: flat but acceptable; multi-view: over-tall / drooping leaves
- nvs hint: multi-view droops: top-down NVS views give the recon no flat-ground constraint, so leaves cascade downward. If multi-view, bias NVS to side/mid elevations.
- photo grade: **good** (2172x2110, dims_ok=True, bg_ok=True, bg_uniformity=0.003)
- VLM: {'growth_form': 'rosette', 'background_ok': True, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'good', 'reasons': 'Classic basal rosette shot perfectly top-down against a clean white background, with the plant centered and filling well over 50% of the frame — fully matching the recommended recipe for a radially-flat rosette form.'} | growth_form_match=True

## maize
- growth form: **graminoid**
- recon mode: **multiview_preferred**
- capture recipe: front, full height; plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: single-image loses thin blades / collapses the canopy
- nvs hint: thin vertical blades need lateral views; default NVS azimuths are adequate
- photo grade: **marginal** (1178x1690, dims_ok=True, bg_ok=False, bg_uniformity=0.258)
- VLM: {'growth_form': 'graminoid', 'background_ok': True, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'good', 'reasons': 'This maize (corn) plant is a graminoid captured front-on at full height against a clean white background, centered and filling well over 50% of the frame, perfectly matching the recipe for this growth form.'} | growth_form_match=True
- reasons: background not plain (high corner colour variance)

## soybean
- growth form: **erect_herb**
- recon mode: **single**
- capture recipe: three-quarter or front, full height; plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: thin stems/petioles may thin out in single-image
- nvs hint: default NVS poses fine; multi-view helps recover occluded stems
- photo grade: **good** (1600x1067, dims_ok=True, bg_ok=True, bg_uniformity=0.003)
- VLM: {'growth_form': 'erect_herb', 'background_ok': True, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'good', 'reasons': 'A young soybean seedling (erect_herb) is photographed front-on at full height against a clean, uniform black background, centered in the frame with the plant filling well over 50% of the frame area, perfectly matching the recipe for this growth form.'} | growth_form_match=True

## tomato
- growth form: **erect_herb**
- recon mode: **single**
- capture recipe: three-quarter or front, full height; plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: thin stems/petioles may thin out in single-image
- nvs hint: default NVS poses fine; multi-view helps recover occluded stems
- photo grade: **marginal** (2000x3000, dims_ok=True, bg_ok=False, bg_uniformity=0.214)
- VLM: {'growth_form': 'erect_herb', 'background_ok': False, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'marginal', 'reasons': 'The plant is a staked cherry tomato (erect_herb) captured at a good three-quarter/front angle with full height and reasonable frame fill, but the background is cluttered with a metal chair, railings, other pots, and foliage, making subject separation difficult for 3D reconstruction.'} | growth_form_match=True
- reasons: background not plain (high corner colour variance); VLM: background_ok is false

## rose
- growth form: **shrub**
- recon mode: **single**
- capture recipe: three-quarter view; plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: interior occlusion; dense bloom can read as a solid blob
- nvs hint: multi-view recovers the occluded interior of a dense bloom canopy
- photo grade: **good** (2048x2048, dims_ok=True, bg_ok=True, bg_uniformity=0.002)
- VLM: {'growth_form': 'shrub', 'background_ok': True, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'good', 'reasons': 'This miniature rose shrub is photographed from a slight three-quarter/front angle against a plain white textured wall, the subject is well-centered and fills well over 50% of the frame, and the background is clean and easily separable — all matching the shrub recipe.'} | growth_form_match=True

## pinus
- growth form: **tree_conifer**
- recon mode: **multiview_required**
- capture recipe: front, full tree; plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: single-image blobs the needle canopy (confirmed on pine)
- nvs hint: needles are a fundamental single-image failure; even multi-view is hard — treat results as low-confidence
- photo grade: **good** (2000x3000, dims_ok=True, bg_ok=True, bg_uniformity=0.003)
- VLM: {'growth_form': 'tree_conifer', 'background_ok': True, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'good', 'reasons': 'The plant is a young potted pine (tree_conifer) shot front-on against a clean white background, centered and filling well over 50% of the frame from pot base to needle tips, matching the recommended front/full-tree recipe perfectly.'} | growth_form_match=True
