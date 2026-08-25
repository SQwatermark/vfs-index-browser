# Unity Humanoid bake oracle

This isolated Unity project evaluates extracted Muscle curves with Unity's own
`HumanPoseHandler`. Its output is a correctness oracle for the static animation
converter; it is not part of the browser's production path.

## Prepare input

```powershell
python tools/build_unity_humanoid_oracle_input.py `
  --model data/internal-cache/134297/models/36304/model.json `
  --avatar data/internal-cache/134297/models/36304/objects/Avatar/<avatar>.json `
  --animation data/internal-cache/144520/manifest-assets/260637/animation/exported/AnimationClip/<clip>.animation.json `
  --output data/local-validation/pelica-oracle-input.json
```

## Run Unity in batch mode

```powershell
& 'C:\Program Files\Unity\Hub\Editor\6000.2.5f1\Editor\Unity.exe' `
  -batchmode -nographics -quit `
  -projectPath experiments/unity_humanoid_oracle `
  -executeMethod HumanoidBakeOracle.Run `
  -oracleInput data/local-validation/pelica-oracle-input.json `
  -oracleOutput data/local-validation/pelica-oracle-output.json `
  -logFile data/local-validation/pelica-oracle.log
```

Use absolute paths for the input, output, and log when invoking the project from
another working directory. The output records every mapped humanoid bone's local
TRS at each source sample time and lists float curves that are not Unity muscle
names. Root motion and non-humanoid Transform curves remain outside this first
validation step.
