shader_type spatial;
render_mode cull_disabled, specular_schlick_ggx, depth_draw_never;
#include "res://AWS_PREFIX/Materials/common.gdshaderinc"

// Waterfall sheet: UV.x across the sheet, UV.y from the lip (0) down to the pool (1).
uniform vec3 water_color : source_color = vec3(0.55, 0.86, 0.95);
uniform float fall_speed = 1.7;

void fragment() {
	float n = aws_noise2(vec2(UV.x * 10.0, UV.y * 4.0 - TIME * fall_speed));
	float n2 = aws_noise2(vec2(UV.x * 23.0 + 7.0, UV.y * 9.0 - TIME * fall_speed * 1.6));
	float streak = smoothstep(0.35, 0.8, n * 0.65 + n2 * 0.35);
	float edge = smoothstep(0.0, 0.14, UV.x) * smoothstep(1.0, 0.86, UV.x);
	float foam = smoothstep(0.82, 1.0, UV.y) + smoothstep(0.1, 0.0, UV.y) * 0.4;
	vec3 col = mix(water_color, vec3(1.0), clamp(streak * 0.8 + foam, 0.0, 1.0));
	ALBEDO = col;
	EMISSION = vec3(0.10, 0.16, 0.20) * (streak + foam);
	ALPHA = clamp((0.58 + 0.38 * streak) * edge + foam * 0.5, 0.0, 1.0);
	ROUGHNESS = 0.12;
	SPECULAR = 0.5;
}
