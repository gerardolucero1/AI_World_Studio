shader_type spatial;
render_mode specular_disabled;
#include "res://AWS_PREFIX/Materials/common.gdshaderinc"

// Vertex-coloured stylized foliage (trees, bushes) with gentle wind sway and toon lighting.
uniform vec3 tint : source_color = vec3(1.0);
uniform float wind_strength = 0.035;
uniform float wind_speed = 1.3;
varying vec3 vcol;

void vertex() {
	vcol = aws_srgb_to_linear(clamp(COLOR.rgb, 0.0, 1.0));
	vec3 origin = MODEL_MATRIX[3].xyz;
	float sway = sin(TIME * wind_speed + origin.x * 0.21 + origin.z * 0.17) + 0.5 * sin(TIME * wind_speed * 2.3 + origin.x * 0.5);
	float h = max(VERTEX.y, 0.0);
	VERTEX.x += sway * wind_strength * h * h;
	VERTEX.z += sway * wind_strength * 0.6 * h * h;
}

void fragment() {
	ALBEDO = vcol * tint;
	ROUGHNESS = 1.0;
}

void light() {
	float nl = dot(NORMAL, LIGHT);
	DIFFUSE_LIGHT += LIGHT_COLOR * aws_ramp(nl * 0.9 + 0.12) * ATTENUATION / PI;
}
