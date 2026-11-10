shader_type spatial;
render_mode specular_disabled;
#include "res://AWS_PREFIX/Materials/common.gdshaderinc"

// 8 surface weights in three opaque RGB splat maps (alpha unused on purpose, see exporter notes).
uniform sampler2D splat0 : filter_linear, repeat_disable;   // R grass, G dirt, B rock
uniform sampler2D splat1 : filter_linear, repeat_disable;   // R snow, G sand, B mud
uniform sampler2D splat2 : filter_linear, repeat_disable;   // R wet_rock, G gravel
uniform vec2 world_size = vec2(1024.0, 1024.0);
uniform vec3 grass_deep : source_color = vec3(0.24, 0.58, 0.14);
uniform vec3 grass_sun : source_color = vec3(0.60, 0.80, 0.20);
uniform vec3 dirt_color : source_color = vec3(0.66, 0.52, 0.32);
uniform vec3 rock_dark : source_color = vec3(0.27, 0.30, 0.38);
uniform vec3 rock_light : source_color = vec3(0.60, 0.58, 0.56);
uniform vec3 snow_color : source_color = vec3(0.96, 0.98, 1.0);
uniform vec3 sand_color : source_color = vec3(0.86, 0.80, 0.55);
uniform vec3 mud_color : source_color = vec3(0.34, 0.25, 0.16);
uniform vec3 gravel_color : source_color = vec3(0.62, 0.60, 0.56);
uniform float rock_displace = 1.3;     // meters of faceting noise on rock faces
uniform float rock_facet = 1.0;        // 0 = smooth rock, 1 = flat-shaded facets

varying vec3 wpos;
varying vec2 wuv;
varying float rockw;

void vertex() {
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
	wuv = wpos.xz / world_size;
	vec3 a = texture(splat0, wuv).rgb;
	vec3 c = texture(splat2, wuv).rgb;
	rockw = clamp(a.b + c.r, 0.0, 1.0);
	// chunky rock faces: displace along the normal with blocky low-frequency noise
	float n = aws_noise3(wpos * vec3(0.085, 0.16, 0.085)) - 0.5;
	VERTEX += NORMAL * n * rock_displace * rockw;
}

void fragment() {
	vec3 a = texture(splat0, wuv).rgb;
	vec3 b = texture(splat1, wuv).rgb;
	vec3 c = texture(splat2, wuv).rgb;
	float t = a.r + a.g + a.b + b.r + b.g + b.b + c.r + c.g;

	float big = aws_noise2(wpos.xz * 0.012);
	float mid = aws_noise2(wpos.xz * 0.08);
	vec3 grass = mix(grass_deep, grass_sun, clamp(big * 0.75 + mid * 0.35, 0.0, 1.0));

	float fade = 1.0 - smoothstep(90.0, 380.0, -VERTEX.z);          // detail vanishes with distance (no shimmering)
	float strata = 0.5 + 0.5 * sin(wpos.y * 0.5 + aws_noise3(wpos * 0.05) * 3.0);
	float rn = aws_noise3(wpos * vec3(0.06, 0.15, 0.06));
	vec3 rock = mix(rock_dark, rock_light, clamp(0.5 + (rn - 0.5) * 0.9 * (0.4 + 0.6 * fade) + (strata - 0.5) * 0.3 * fade, 0.0, 1.0));
	vec3 wn = normalize((INV_VIEW_MATRIX * vec4(NORMAL, 0.0)).xyz);
	rock *= mix(0.62, 1.12, smoothstep(0.25, 0.9, wn.y));       // sheer faces darker, ledges and tops lighter
	vec3 wet = rock * 0.62;

	vec3 col = grass * a.r + dirt_color * a.g + rock * a.b + snow_color * b.r + sand_color * b.g + mud_color * b.b + wet * c.r + gravel_color * c.g;
	ALBEDO = col / max(t, 0.001);
	ROUGHNESS = 1.0;

	// flat facets on rock faces
	vec3 fn = normalize(cross(dFdx(VERTEX), dFdy(VERTEX)));
	if (dot(fn, VIEW) < 0.0) fn = -fn;
	NORMAL = normalize(mix(NORMAL, fn, clamp(rockw * rock_facet * 1.5, 0.0, 1.0) * fade));
}

void light() {
	float nl = dot(NORMAL, LIGHT);
	DIFFUSE_LIGHT += LIGHT_COLOR * aws_ramp(nl) * ATTENUATION / PI;
}
