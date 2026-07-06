# CLIP/BioCLIP feasibility probe — run `photo2`

13 labeled items.

## Mechanism: bioclip

| defect | tp | fp | tn | fn | precision | recall |
|---|---|---|---|---|---|---|
| fruit_only | 0 | 0 | 12 | 1 | nan | 0.00 |
| wrong_species | 0 | 0 | 10 | 3 | nan | 0.00 |

## Mechanism: clip

| defect | tp | fp | tn | fn | precision | recall |
|---|---|---|---|---|---|---|
| fruit_only | 1 | 11 | 1 | 0 | 0.08 | 1.00 |
| wrong_species | 0 | 0 | 10 | 3 | nan | 0.00 |

## Mechanism: vlm

| defect | tp | fp | tn | fn | precision | recall |
|---|---|---|---|---|---|---|
| fruit_only | 0 | 1 | 11 | 1 | 0.00 | 0.00 |
| wrong_species | 0 | 0 | 10 | 3 | nan | 0.00 |
