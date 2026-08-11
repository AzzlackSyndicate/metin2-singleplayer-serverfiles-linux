# fix_effect_shader_precision*.INEFFECTIVE.py.txt

Stillgelegt, **nicht** gelöscht — die Messung dahinter ist wertvoll, die Änderung nicht.

`precision highp float/sampler2D` im Shaderquelltext regiert nur Deklarationen ohne
eigenen Qualifizierer. Die Temporaries, die der glsl-optimizer selbst erzeugt
(`lowp float a_1`, `lowp vec3 c_2`, `lowp vec4 tmpvar_3`), regiert es nicht — und die
sind die ganze Rechnung. Am Blob gemessen, mit `shaderc` direkt, ohne Bau.

Gleich mitgemessen und ebenso widerlegt: `--profile 300_es` ergibt
`lowp=6 mediump=1 highp=4`. Der Wechsel von ESSL 1.00 auf 3.00 hätte das additive
Glühen **nicht** repariert, obwohl er als „die Wurzel" eingeplant war.

Die Dateiendung ist `.txt`, damit ein pauschaler Lauf über `fix_*.py` sie nicht
wieder einspielt. Genau das ist einmal passiert.
