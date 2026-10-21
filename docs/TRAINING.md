# Model training & dataset guide

Target: a detector that reliably finds **people** and **mobile phones** in your
classrooms, including small, oblique, partially occluded phones and hard negatives
(books, calculators, remotes, water bottles, empty hands).

## 1. Before you collect anything

* Written authorisation from the institution (purpose, scope, retention, who can
  view footage).
* Consent/notification for anyone recorded, consistent with local law (GDPR, India
  DPDP Act, FERPA/COPPA-style rules for minors).
* A safe storage location with access control and a deletion date.
* A rule that **no faces are published** and no dataset is shared without review.
* Annotation guidance agreed by two people (annotation rules differ wildly between
  annotators and directly determine precision/recall).

## 2. Data collection plan

| Bucket | What to record | Why |
|---|---|---|
| Camera positions | Every installed camera angle (wide room, front, side, rear) | A model trained on one angle transfers poorly |
| Lighting | Morning, midday, blinds open/closed, evening, lights off (projector only) | Low-light recall is the most common failure |
| Occupancy | 0, 1-5, 10-20, 30-50 people | Dense scenes create occlusion and ID swaps |
| Phone poses | Held to ear, both hands, under desk, resting on desk, screen visible/not | Different association evidence |
| Hard negatives | Books, notebooks, calculators, remotes, bottles, tablets, hands empty, headphones | Prevent "phone" false positives |
| Distance | Near (2-4 m) and far (8-15 m) seating | Phone pixel size changes 5-10× |

Suggested starting size for fine-tuning: **2,000-6,000 labelled frames** from
representative sessions. More important than total count: coverage of the buckets
above and **at least 400-800 phone instances**.

Split by **session** (not by frame) so near-duplicate frames do not leak into
validation. `scripts/prepare_dataset.py` groups by filename prefix before splitting.

## 3. Annotation rules (YOLO bounding boxes)

* Classes: `0 = person`, `67 = cell phone` (keep COCO ids so the pretrained head
  transfers without remapping; `scripts/prepare_dataset.py` writes this mapping).
* **Person**: full visible body; if seated behind a desk, include the desk-line
  occlusion boundary — do not include the desk itself. Label partially visible
  people (≥ ~30 % visible).
* **Cell phone**: tight box on the device, including the hand only if it hides the
  device edge. Label phones that are clearly recognisable as a phone (screen, body
  shape). If you cannot tell it is a phone at 2× zoom, **do not label it** — this
  is precisely the "uncertain" case the running system is designed to report.
* Exclude: tablets/laptops (label separately if you add classes), mirrors, TV
  remotes, calculators, wallets.
* One box per object; no boxes on reflections or posters showing phones.
* Double-annotate 5 % of images and measure agreement (target IoU-based agreement
  > 0.8 for phones).

## 4. Dataset layout and validation

```bash
cd backend
python scripts/prepare_dataset.py --src datasets/raw --out datasets/classroom \
        --val 0.15 --test 0.15
```

```
datasets/classroom/
  data.yaml
  images/{train,val,test}/*.jpg
  labels/{train,val,test}/*.txt
```

The script validates coordinate ranges, class ids and missing labels, prints class
balance, and refuses to split a dataset with structural errors. Use `--check-only`
to re-validate after changes.

## 5. Training

```bash
python scripts/train.py --data datasets/classroom/data.yaml \
    --model yolo11n.pt --epochs 80 --imgsz 640 --batch 8 --device 0
```

Default augmentation is classroom-appropriate (mosaic, mild scale/HSV, small
rotation). Notes:

* Use `yolo11s.pt` + `--imgsz 960` if phones are small in frame and VRAM allows
  (roughly 4× compute vs nano at 640).
* Keep `--patience 20`; classroom datasets overfit quickly to one room.
* Class imbalance is handled with `cls=0.7`; additionally oversample phone-containing
  frames or add copy-paste augmentation of phones into desk scenes.
* Freeze nothing by default: full fine-tuning worked best in our experiments, but
  `--freeze 10` is worth trying for very small datasets.

Training artefacts: `runs/classroom/yolo_classroom/{weights/best.pt, results.png,
confusion_matrix.png, PR_curve.png}`.

## 6. Validation and operating point

```bash
python scripts/evaluate.py --data datasets/classroom --split val --conf 0.25
python scripts/evaluate.py --data datasets/classroom --split test --conf 0.35
```

Outputs precision/recall/F1/AP50, a confidence sweep, and a confusion matrix
(SVG + Markdown). Choose the operating point per camera:

| Camera type | Suggested `phone_conf` | Suggested `persistence_threshold` |
|---|---|---|
| Close-up (phone ≥ 30 px) | 0.35 | 0.60 |
| Wide room (phone 15-25 px) | 0.25 | 0.70 |
| Low light / IR | 0.30 | 0.75 (expect lower recall) |

Raise thresholds until the alert rate on **negative footage** (no phone use) is
near zero. Alert-level precision matters more than box-level AP.

## 7. Deploy the model

```bash
python scripts/export_onnx.py --model runs/classroom/yolo_classroom/weights/best.pt \
       --out /opt/scai/models/classroom.onnx
```

```ini
SCAI_DETECTOR_BACKEND=onnx      # or auto/ultralytics for the .pt
SCAI_ONNX_MODEL_PATH=/opt/scai/models/classroom.onnx
SCAI_MODEL_IMGSZ=640
```

Reload from the dashboard (**Settings → Detector → Reload**) and verify
`/api/system/info` reports `phone_capable: true`, `degraded: false`.

## 8. Continuous improvement loop

1. Review `uncertain` and `suppressed` events weekly (Events page → review note).
2. Export the reviewed window (not the video) and pull the corresponding frames from
   your authorised recording system.
3. Correct labels, add to `datasets/raw`, re-run prepare → train → evaluate.
4. A/B test the new weights on one camera before rolling out.
5. Retrain when the room layout, camera or lighting changes materially.

## 9. Licensing of models and datasets

* **Ultralytics YOLO**: AGPL-3.0 by default; a commercial licence is available. If
  AGPL is incompatible with your distribution model, use a permissively licensed
  detector (e.g. YOLOX Apache-2.0, RT-DETR where applicable, or a MobileNet-SSD
  Apache-2.0) exported to ONNX and point `SCAI_ONNX_MODEL_PATH` at it. The pipeline
  only needs `person` and `cell phone` class ids.
* **Datasets**: COCO (CC BY 4.0 annotations / Flickr images with mixed licences) is
  fine for pretraining, not for redistributing. Classroom footage you record is
  yours to govern — never publish it with identifiable students.
* Record the licence and provenance of every weight file in your deployment notes
  (`data/models/PROVENANCE.md` is a good place).
