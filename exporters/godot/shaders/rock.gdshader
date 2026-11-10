shader_type spatial;
render_mode specular_disabled;
#include "res://AWS_PREFIX/Materials/common.gdshaderinc"

uniform vec3 tint : source_color = vec3(1.0);
varying vec3 vcol;
varying vec3 wpos;

void vertex() {
	vcol = aws_srgb_to_linear(clamp(COLOR.rgb, 0.0, 1.0));
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	float n = aws_noise3(wpos * 0.9);
	ALBEDO = vcol * tint * (0.88 + 0.24 * n);
	ROUGHNESS = 1.0;
	vec3 fn = normalize(cross(dFdx(VERTEX), dFdy(VERTEX)));
	if (dot(fn, VIEW) < 0.0) fn = -fn;
	NORMAL = fn;                       // faceted low-poly look
}

void light() {
	float nl = dot(NORMAL, LIGHT);
	DIFFUSE_LIGHT += LIGHT_COLOR * aws_ramp(nl) * ATTENUATION / PI;
}
