# Reflex layer uses class-agnostic depth, not object detection

The reflex layer of an **Autopilot session** needs to answer "is the forward path blocked, and which way is clearer?" — not "what class of thing is in front of me?" We use a monocular depth model (Depth Anything V2 Small) reduced to three vertical clearance strips, because off-the-shelf object detectors (YOLO + COCO) have no `wall` class and would silently fail on the most common indoor obstacle.

## Considered Options

- **Stock object detection (YOLO + COCO).** Rejected: `wall` is not a COCO class, so the headline obstacle case is unhandled. Also gives the reflex layer more information than it needs (a class label) while missing things it does need (unusual or untrained obstacles).
- **Open-vocabulary detection (YOLO-World, OWL-ViT).** Solves the wall problem and supports future per-class behavior in one model, at the cost of heavier weights and prompt tuning. Reasonable alternative; depth wins on simplicity for the reflex-only scope.
- **Floor / drivable-surface segmentation (ADE20K).** Cleaner conceptually ("the floor is the road") but brittle to camera mount, floor materials, and obstacles that don't reduce to floor-vs-not-floor (a low table is still a floor patch underneath but you'd drive into the tabletop).
- **Depth (chosen).** Class-agnostic, robust to mount and environment, gives a continuous signal that maps directly onto the three-strip clearance reduction the **Reflex driving** policy needs.

## Consequences

- A future contributor seeing the perception code may want to "improve" reflex by swapping in a class-aware detector. That would be a regression for walls and any unknown obstacle, and is the reason this ADR exists.
- Naming or announcing specific objects (e.g., "toy on the floor before vacuuming") is a separate, future concern. It will run as a second model alongside reflex, not by replacing the reflex layer.
