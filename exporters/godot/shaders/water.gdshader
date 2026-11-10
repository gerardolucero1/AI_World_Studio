shader_type spatial;
render_mode specular_schlick_ggx, depth_draw_never;
#include "res://AWS_PREFIX/Materials/common.gdshaderinc"

uniform sampler2D depth_tex : hint_depth_texture, repeat_disable, filter_nearest;
uniform vec3 shallow_color : source_color = vec3(0.20, 0.86, 0.80);
uniform vec3 deep_color : source_color = vec3(0.03, 0.42, 0.66);
uniform float depth_range = 5.0;
uniform float foam_width = 0.55;
uniform float flow_speed = 0.05;
varying vec3 wpos;

void vertex() {
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	float depth = texture(depth_tex, SCREEN_UV).r;
	vec3 ndc = vec3(SCREEN_UV * 2.0 - 1.0, depth);
	vec4 view = INV_PROJECTION_MATRIX * vec4(ndc, 1.0);
	view.xyz /= view.w;
	float thick = max(VERTEX.z - view.z, 0.0);          // water column under this pixel (m)

	float ripple = aws_noise2(wpos.xz * 0.35 + vec2(TIME * flow_speed, TIME * flow_speed * 0.6))
		+ 0.5 * aws_noise2(wpos.xz * 0.9 - vec2(TIME * flow_speed * 1.4, 0.0));
	float d = clamp(thick / depth_range, 0.0, 1.0);
	vec3 col = mix(shallow_color, deep_color, pow(d, 0.7));
	col += vec3(0.10) * smoothstep(0.55, 0.95, ripple);          // glints
	float foam = 1.0 - smoothstep(0.0, foam_width, thick + (ripple - 0.7) * 0.25);
	ALBEDO = mix(col, vec3(1.0), clamp(foam, 0.0, 1.0));
	ALPHA = mix(0.55, 0.96, clamp(d * 1.8, 0.0, 1.0)) + foam * 0.3;
	ALPHA = clamp(ALPHA, 0.0, 1.0);
	ROUGHNESS = 0.06;
	METALLIC = 0.0;
	SPECULAR = 0.6;
	NORMAL_MAP = vec3(0.5 + (ripple - 0.7) * 0.15, 0.5 + (ripple - 0.7) * 0.1, 1.0);
	NORMAL_MAP_DEPTH = 0.6;
}
