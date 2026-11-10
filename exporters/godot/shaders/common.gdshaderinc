// Shared helpers (value noise, cel-shaded light ramp). Included by the AI World Studio shaders.
float aws_hash31(vec3 p) {
	p = fract(p * 0.1031);
	p += dot(p, p.zyx + 31.32);
	return fract((p.x + p.y) * p.z);
}
float aws_noise3(vec3 x) {
	vec3 i = floor(x); vec3 f = fract(x);
	f = f * f * (3.0 - 2.0 * f);
	return mix(mix(mix(aws_hash31(i), aws_hash31(i + vec3(1,0,0)), f.x),
	               mix(aws_hash31(i + vec3(0,1,0)), aws_hash31(i + vec3(1,1,0)), f.x), f.y),
	           mix(mix(aws_hash31(i + vec3(0,0,1)), aws_hash31(i + vec3(1,0,1)), f.x),
	               mix(aws_hash31(i + vec3(0,1,1)), aws_hash31(i + vec3(1,1,1)), f.x), f.y), f.z);
}
float aws_noise2(vec2 p) { return aws_noise3(vec3(p, 0.37)); }
// Soft two-band toon ramp.
float aws_ramp(float nl) {
	return smoothstep(-0.08, 0.16, nl) * 0.62 + smoothstep(0.28, 0.58, nl) * 0.38;
}

// Vertex colours are authored in sRGB; Godot hands them to shaders as-is, so convert.
vec3 aws_srgb_to_linear(vec3 c) { return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c)); }
