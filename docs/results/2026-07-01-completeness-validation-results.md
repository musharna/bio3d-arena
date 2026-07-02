# Completeness metric — validation results

**Verdict:** binary complete/incomplete kappa = 0.6360595925813317  →  PASS (>= 0.6)

## Headline
- eval outputs (in BOTH human GT and metric prediction): **114**
- **binary complete/incomplete kappa: 0.6360595925813317**  (the PASS gate)
- 4-way category kappa: 0.42344827586206896  (experimental — small per-class n)
- isolated-organ recall: 0.8888888888888888  (experimental)
- GT outputs with no metric prediction (dropped): 0

## Interpretation
- Binary agreement kappa=0.636 — MODERATE, clearing the preregistered 0.6 gate → the metric is VALIDATED.
- Isolated-organ recall 0.89 — the core capability (flagging lone-organ outputs) works. The main disagreement is human blanket 'not a plant / junk' labels (→fragment) vs the metric's literal organ detection (→isolated/complete): a fragment/complete boundary + GT-philosophy gap, the clear target for a v1.1 iteration (prompt/inventory or GT-definition alignment).

## Distributions
- human GT (all labeled outputs, n=114): {'fragment': 51, 'isolated-organ': 9, 'partial-organism': 5, 'complete': 49}
- metric prediction on the eval set: {'fragment': 19, 'isolated-organ': 31, 'complete': 62, 'partial-organism': 2}
- binary confusion (gt, pred) on eval set: {'complete->complete': 45, 'complete->incomplete': 4, 'incomplete->complete': 17, 'incomplete->incomplete': 48}

## Methodology (auditable)
- **Human GT, not VLM:** incompleteness categories are mapped from the human free-text
  `note` column via an auditable keyword table (app/completeness_validation.py); `complete`
  is a positive label from a human `present_correct` trait verdict with no incompleteness
  note. The prior VLM judge's `vlm_rationale` is deliberately NOT used (it would be circular).
- **View-parity:** the metric was scored from the SAME contact sheets the humans labeled
  from (the calibration `multi4` sheets), via scripts/score_completeness_from_sheets.py, so
  the comparison isolates organ detection from any rendering difference. Production scoring
  uses a fresh `turntable` sheet.
- **Binary is the robust headline;** the 4-way kappa and isolated-organ recall are reported
  but experimental because the calibration corpus is thin in the isolated-organ /
  partial-organism classes.

## Auditable GT (per output: winning category)
- output 60: gt=fragment  pred=isolated-organ
- output 61: gt=fragment  pred=isolated-organ
- output 62: gt=complete  pred=complete
- output 63: gt=complete  pred=complete
- output 69: gt=complete  pred=complete
- output 72: gt=isolated-organ  pred=isolated-organ
- output 73: gt=fragment  pred=isolated-organ
- output 80: gt=fragment  pred=complete
- output 81: gt=fragment  pred=complete
- output 82: gt=fragment  pred=complete
- output 83: gt=fragment  pred=complete
- output 89: gt=fragment  pred=isolated-organ
- output 92: gt=fragment  pred=isolated-organ
- output 93: gt=fragment  pred=fragment
- output 94: gt=fragment  pred=fragment
- output 96: gt=fragment  pred=fragment
- output 98: gt=fragment  pred=fragment
- output 100: gt=fragment  pred=isolated-organ
- output 103: gt=fragment  pred=fragment
- output 112: gt=complete  pred=isolated-organ
- output 113: gt=fragment  pred=isolated-organ
- output 115: gt=isolated-organ  pred=isolated-organ
- output 117: gt=fragment  pred=isolated-organ
- output 118: gt=isolated-organ  pred=isolated-organ
- output 119: gt=fragment  pred=isolated-organ
- output 120: gt=fragment  pred=isolated-organ
- output 121: gt=isolated-organ  pred=isolated-organ
- output 122: gt=fragment  pred=isolated-organ
- output 123: gt=isolated-organ  pred=isolated-organ
- output 124: gt=isolated-organ  pred=isolated-organ
- output 127: gt=isolated-organ  pred=isolated-organ
- output 128: gt=complete  pred=fragment
- output 129: gt=fragment  pred=isolated-organ
- output 131: gt=isolated-organ  pred=isolated-organ
- output 148: gt=complete  pred=complete
- output 149: gt=fragment  pred=complete
- output 150: gt=complete  pred=complete
- output 151: gt=complete  pred=complete
- output 152: gt=complete  pred=complete
- output 153: gt=complete  pred=complete
- output 154: gt=complete  pred=complete
- output 155: gt=complete  pred=complete
- output 156: gt=complete  pred=complete
- output 157: gt=complete  pred=complete
- output 158: gt=complete  pred=complete
- output 159: gt=fragment  pred=fragment
- output 160: gt=complete  pred=complete
- output 161: gt=complete  pred=complete
- output 162: gt=complete  pred=complete
- output 163: gt=complete  pred=complete
- output 164: gt=fragment  pred=complete
- output 178: gt=fragment  pred=complete
- output 179: gt=complete  pred=complete
- output 180: gt=complete  pred=complete
- output 181: gt=fragment  pred=partial-organism
- output 182: gt=complete  pred=complete
- output 192: gt=complete  pred=complete
- output 193: gt=partial-organism  pred=isolated-organ
- output 194: gt=complete  pred=complete
- output 195: gt=complete  pred=complete
- output 196: gt=fragment  pred=fragment
- output 197: gt=complete  pred=complete
- output 200: gt=fragment  pred=isolated-organ
- output 201: gt=fragment  pred=partial-organism
- output 202: gt=partial-organism  pred=complete
- output 203: gt=partial-organism  pred=complete
- output 210: gt=partial-organism  pred=isolated-organ
- output 211: gt=isolated-organ  pred=fragment
- output 215: gt=fragment  pred=fragment
- output 216: gt=fragment  pred=complete
- output 244: gt=complete  pred=complete
- output 245: gt=complete  pred=complete
- output 246: gt=fragment  pred=complete
- output 248: gt=complete  pred=complete
- output 249: gt=complete  pred=complete
- output 253: gt=complete  pred=complete
- output 254: gt=fragment  pred=fragment
- output 255: gt=complete  pred=complete
- output 256: gt=fragment  pred=fragment
- output 257: gt=complete  pred=complete
- output 259: gt=fragment  pred=fragment
- output 260: gt=fragment  pred=complete
- output 261: gt=complete  pred=complete
- output 262: gt=fragment  pred=fragment
- output 264: gt=complete  pred=complete
- output 265: gt=complete  pred=complete
- output 266: gt=fragment  pred=fragment
- output 267: gt=complete  pred=complete
- output 268: gt=complete  pred=complete
- output 269: gt=complete  pred=complete
- output 270: gt=complete  pred=complete
- output 271: gt=complete  pred=complete
- output 272: gt=complete  pred=complete
- output 275: gt=complete  pred=isolated-organ
- output 279: gt=complete  pred=complete
- output 289: gt=fragment  pred=fragment
- output 290: gt=partial-organism  pred=complete
- output 291: gt=fragment  pred=fragment
- output 292: gt=fragment  pred=fragment
- output 293: gt=fragment  pred=complete
- output 294: gt=fragment  pred=isolated-organ
- output 295: gt=fragment  pred=isolated-organ
- output 297: gt=fragment  pred=fragment
- output 300: gt=complete  pred=complete
- output 301: gt=fragment  pred=complete
- output 303: gt=fragment  pred=complete
- output 304: gt=complete  pred=complete
- output 305: gt=fragment  pred=complete
- output 306: gt=complete  pred=complete
- output 307: gt=complete  pred=complete
- output 309: gt=fragment  pred=isolated-organ
- output 313: gt=fragment  pred=isolated-organ
- output 318: gt=fragment  pred=isolated-organ
- output 319: gt=complete  pred=isolated-organ
