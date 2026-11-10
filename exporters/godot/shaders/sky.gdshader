shader_type sky;
uniform vec3 top_color : source_color = vec3(0.14, 0.42, 0.90);
uniform vec3 horizon_color : source_color = vec3(0.70, 0.86, 0.98);
uniform vec3 ground_color : source_color = vec3(0.55, 0.68, 0.72);
uniform float cloud_cover = 0.5;
uniform float cloud_speed = 0.004;

float h21(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float vn(vec2 p) {
	vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
	return mix(mix(h21(i), h21(i + vec2(1, 0)), f.x), mix(h21(i + vec2(0, 1)), h21(i + vec2(1, 1)), f.x), f.y);
}
float fbm(vec2 p) { float a = 0.5, s = 0.0; for (int i = 0; i < 5; i++) { s += a * vn(p); p *= 2.03; a *= 0.5; } return s; }

void sky() {
	vec3 d = EYEDIR;
	float up = clamp(d.y, 0.0, 1.0);
	vec3 col = mix(horizon_color, top_color, pow(up, 0.55));
	if (d.y < 0.0) col = mix(horizon_color, ground_color, clamp(-d.y * 4.0, 0.0, 1.0));
	if (d.y > 0.01) {
		vec2 uv = d.xz / (d.y + 0.22) * 1.1 + vec2(TIME * cloud_speed, 0.0);
		float c = fbm(uv * 1.6);
		float shape = smoothstep(1.0 - cloud_cover, 1.0 - cloud_cover + 0.22, c);
		float light = clamp(0.62 + 0.55 * (fbm(uv * 1.6 + vec2(0.07, 0.11)) - c) * 6.0, 0.5, 1.0);
		vec3 ccol = mix(vec3(0.72, 0.80, 0.93), vec3(1.0), light);
		col = mix(col, ccol, shape * smoothstep(0.01, 0.22, d.y));
	}
	COLOR = col;
}
