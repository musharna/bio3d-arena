# Plant Input Advisor — report

## arabidopsis
- growth form: **rosette**
- recon mode: **single**
- capture recipe: top-down (radially flat — natural for a rosette); plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: single-image: flat but acceptable; multi-view: over-tall / drooping leaves
- nvs hint: multi-view droops: top-down NVS views give the recon no flat-ground constraint, so leaves cascade downward. If multi-view, bias NVS to side/mid elevations.
- photo grade: **good** (2172x2110, dims_ok=True, bg_ok=True, bg_uniformity=0.003)
- VLM: {'growth_form': 'rosette', 'background_ok': True, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'good', 'reasons': 'This is a classic basal rosette captured from directly overhead (top-down) against a clean white background, with the plant centered and filling well over 50% of the frame — perfectly matching the recommended recipe for a radially-flat rosette form.'} | growth_form_match=True

## maize
- growth form: **graminoid**
- recon mode: **multiview_preferred**
- capture recipe: front, full height; plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: single-image loses thin blades / collapses the canopy
- nvs hint: thin vertical blades need lateral views; default NVS azimuths are adequate
- photo grade: **marginal** (1178x1690, dims_ok=True, bg_ok=False, bg_uniformity=0.258)
- VLM: {'growth_form': 'graminoid', 'background_ok': True, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'good', 'reasons': 'This corn plant (Zea mays, a tall graminoid) is photographed front-on at full height against a clean white background, centered and filling well over 50% of the frame — perfectly matching the graminoid recipe.'} | growth_form_match=True
- reasons: background not plain (high corner colour variance)

## soybean
- growth form: **erect_herb**
- recon mode: **single**
- capture recipe: three-quarter or front, full height; plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: thin stems/petioles may thin out in single-image
- nvs hint: default NVS poses fine; multi-view helps recover occluded stems
- photo grade: **good** (1600x1067, dims_ok=True, bg_ok=True, bg_uniformity=0.003)
- VLM: {'growth_form': 'erect_herb', 'background_ok': True, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'good', 'reasons': 'A young soybean seedling (erect herb) is photographed front-on at full height against a clean black background, centered and filling well over 50% of the frame, with soft, even studio lighting that cleanly separates the subject — perfectly matching the recommended recipe.'} | growth_form_match=True

## tomato
- growth form: **erect_herb**
- recon mode: **single**
- capture recipe: three-quarter or front, full height; plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: thin stems/petioles may thin out in single-image
- nvs hint: default NVS poses fine; multi-view helps recover occluded stems
- photo grade: **marginal** (2000x3000, dims_ok=True, bg_ok=False, bg_uniformity=0.214)
- VLM: {'growth_form': 'erect_herb', 'background_ok': False, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'marginal', 'reasons': 'The plant is a potted cherry tomato — a classic erect herb — shown full-height from a slight three-quarter/front angle (matching the recipe), and it fills well over 50% of the frame; however, the background is cluttered with a wrought-iron chair, railing, other pots, and foliage, making subject/background separation very difficult for 3-D reconstruction.'} | growth_form_match=True
- reasons: background not plain (high corner colour variance); VLM: background_ok is false

## rose
- growth form: **shrub**
- recon mode: **single**
- capture recipe: three-quarter view; plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: interior occlusion; dense bloom can read as a solid blob
- nvs hint: multi-view recovers the occluded interior of a dense bloom canopy
- photo grade: **good** (2048x2048, dims_ok=True, bg_ok=True, bg_uniformity=0.002)
- VLM: {'growth_form': 'shrub', 'background_ok': True, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'good', 'reasons': 'This miniature rose shrub is photographed from a slight three-quarter/front angle against a plain white textured wall, the subject is well-centered and fills well over 50% of the frame, with soft, even lighting — closely matching the recommended recipe for a shrub.'} | growth_form_match=True

## pinus
- growth form: **tree_conifer**
- recon mode: **multiview_required**
- capture recipe: front, full tree; plain/neutral background; subject centered, fills >50% of frame, soft even light; >=1024px
- expected failure: single-image blobs the needle canopy (confirmed on pine)
- nvs hint: needles are a fundamental single-image failure; even multi-view is hard — treat results as low-confidence
- photo grade: **marginal** (2848x4272, dims_ok=True, bg_ok=False, bg_uniformity=0.218)
- VLM: {'growth_form': 'tree_conifer', 'background_ok': False, 'view_matches_recipe': True, 'fill_ok': True, 'verdict': 'marginal', 'reasons': 'The plant is correctly identified as a conifer (Scots pine, Pinus sylvestris) and the front/full-tree view matches the recipe with the subject filling the frame well, but the background is cluttered — other shrubs, trees, a white structure, and a bench are visible behind the trunk, making the subject difficult to isolate cleanly for 3D reconstruction.'} | growth_form_match=True
- reasons: background not plain (high corner colour variance); VLM: background_ok is false
