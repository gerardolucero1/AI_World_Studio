shader_type spatial;
render_mode specular_schlick_ggx, depth_draw_never;
#include "res://AWS_PREFIX/Materials/common.gdshaderinc"

// Renderer-agnostic water (works in Compatibility too: no depth texture). Stylized turquoise with moving glints.
uniform vec3 shallow_color : source_color = vec3(0.16, 0.74, 0.74);
uniform vec3 deep_color : source_color = vec3(0.04, 0.45, 0.68);
uniform float flow_speed = 0.05;
varying vec3 wpos;

void vertex() { wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }

void fragment() {
	float ripple = aws_noise2(wpos.xz * 0.35 + vec2(TIME * flow_speed, TIME * flow_speed * 0.6))
		+ 0.5 * aws_noise2(wpos.xz * 0.9 - vec2(TIME * flow_speed * 1.4, 0.0));
	float fres = pow(1.0 - clamp(dot(NORMAL, VIEW), 0.0, 1.0), 2.0);
	vec3 col = mix(shallow_color, deep_color, 0.35 + 0.65 * fres);
	col += vec3(0.10) * smoothstep(0.55, 0.95, ripple);
	ALBEDO = col;
	ALPHA = 0.78;
	ROUGHNESS = 0.08;
	SPECULAR = 0.6;
}
