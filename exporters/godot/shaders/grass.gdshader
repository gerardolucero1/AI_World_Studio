shader_type spatial;
render_mode cull_disabled, specular_disabled;
#include "res://AWS_PREFIX/Materials/common.gdshaderinc"

// GPU-placed tall grass. Every chunk draws the same patch of tufts; the shader lifts each tuft onto the terrain (height texture),
// hides it where the ground is not grassy (splat) or is water, and shrinks it with distance. Nothing per-tuft is stored in the scene.
uniform sampler2D height_tex : filter_nearest, repeat_disable;   // RG8: R = high byte, G = low byte of the 16-bit height
uniform sampler2D splat0 : filter_linear, repeat_disable;        // R = grass weight
uniform sampler2D splat2 : filter_linear, repeat_disable;        // B = water mask
uniform vec2 world_size = vec2(1024.0, 1024.0);
uniform vec2 tex_size = vec2(513.0, 513.0);
uniform float cell_size = 2.0;
uniform float h_min = 0.0;
uniform float h_max = 100.0;
uniform float fade_start = 38.0;
uniform float fade_end = 68.0;
uniform float blade_height = 0.95;
uniform float wind_strength = 0.22;
uniform float wind_speed = 1.6;
uniform vec3 grass_deep : source_color = vec3(0.24, 0.58, 0.14);
uniform vec3 grass_sun : source_color = vec3(0.60, 0.80, 0.20);

varying vec3 vcol;

float dec(vec4 t) { return h_min + (t.r * 255.0 * 256.0 + t.g * 255.0) / 65535.0 * (h_max - h_min); }

float height_at(vec2 xz) {
	vec2 p = xz / cell_size;
	vec2 f = fract(p);
	ivec2 i0 = ivec2(floor(p));
	ivec2 mx = ivec2(tex_size) - ivec2(1);
	float h00 = dec(texelFetch(height_tex, clamp(i0, ivec2(0), mx), 0));
	float h10 = dec(texelFetch(height_tex, clamp(i0 + ivec2(1, 0), ivec2(0), mx), 0));
	float h01 = dec(texelFetch(height_tex, clamp(i0 + ivec2(0, 1), ivec2(0), mx), 0));
	float h11 = dec(texelFetch(height_tex, clamp(i0 + ivec2(1, 1), ivec2(0), mx), 0));
	return mix(mix(h00, h10, f.x), mix(h01, h11, f.x), f.y);
}

void vertex() {
	vec3 base = MODEL_MATRIX[3].xyz;                       // world position of this tuft
	vec2 uv = base.xz / world_size;
	float rnd = aws_hash31(vec3(base.xz * 1.37, 3.1));
	float cover = texture(splat0, uv).r;
	float water = texture(splat2, uv).b;
	float ok = smoothstep(0.5, 0.85, cover) * (1.0 - smoothstep(0.05, 0.3, water));
	ok *= step(rnd * 0.7, cover);                            // thin out towards the edge of the meadow
	float d = distance(base, CAMERA_POSITION_WORLD);
	float s = ok * (1.0 - smoothstep(fade_start, fade_end, d)) * (0.7 + 0.6 * fract(rnd * 7.31));
	vec3 v = VERTEX * s;
	v.y *= blade_height;
	float sway = sin(TIME * wind_speed + base.x * 0.35 + base.z * 0.27) + 0.5 * sin(TIME * wind_speed * 2.1 + base.x * 0.9);
	v.x += sway * wind_strength * v.y * v.y;
	v.z += sway * wind_strength * 0.6 * v.y * v.y;
	v.y += (height_at(base.xz) - base.y) / max(length(MODEL_MATRIX[1].xyz), 0.0001);   // lift onto the terrain
	VERTEX = v;
	float big = aws_noise2(base.xz * 0.012);
	float mid = aws_noise2(base.xz * 0.08);
	vec3 tint = mix(grass_deep, grass_sun, clamp(big * 0.75 + mid * 0.35, 0.0, 1.0));   // same variation as the terrain: tufts blend into the ground
	vcol = aws_srgb_to_linear(clamp(COLOR.rgb, 0.0, 1.2)) * tint * (0.92 + 0.16 * rnd);
}

void fragment() {
	ALBEDO = vcol;
	ROUGHNESS = 1.0;
	NORMAL = normalize(mix(NORMAL, (VIEW_MATRIX * vec4(0.0, 1.0, 0.0, 0.0)).xyz, 0.75));
}

void light() {
	float nl = dot(NORMAL, LIGHT);
	DIFFUSE_LIGHT += LIGHT_COLOR * aws_ramp(nl * 0.8 + 0.25) * ATTENUATION / PI;
}
