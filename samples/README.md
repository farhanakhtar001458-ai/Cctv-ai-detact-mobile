# Sample media

Generated content lives here; it is not committed (see `.gitignore`).

Create a simulated classroom clip for file-based testing:

```bash
cd backend
python scripts/generate_demo_video.py --out ../samples/sim_classroom.mp4 --seconds 60
```

Then register it as a camera: **Cameras → Add camera → Source type "Local video file"**
and paste the absolute path.

Place any real CCTV clips you are authorised to use in `samples/private/`
(git-ignored) and never commit footage of identifiable people.
