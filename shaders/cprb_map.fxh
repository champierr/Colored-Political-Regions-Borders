//////////////////////////////////////////////////////////////////////////////////////////////////
// Colored Political Regions & Borders (CPR&B)
// Political map drawn inside the world map terrain shaders: every pixel takes the colour of the
// owner of the nearest center, with a fixed dark line and a glow of each side's colour where two
// owners meet. Included from mb.fx (MAP_SHADERS section).
//
// Every frame the module sends (script cprb_update_map, added by the CPR&B Map Patcher):
//   cprbpK    = centers 2K and 2K+1: (xpack, yq, xpack, yq)
//               xq, yq = round((coordinate + 2048) * 4), xpack = owner faction * 16384 + xq
//   cprbcK    = colours of factions 4K..4K+3, each red7 * 16384 + green7 * 128 + blue7
//   cprbopacity = fill strength in percent (the in-game Ctrl+1 slider)
//   cprbunit  = 1
// The game may divide every value by its fixed point multiplier; dividing by the cprbunit we
// receive undoes that. cprbunit stays 0 until the module has sent data, which switches the overlay
// off. Unused center slots stay 0, which puts them far outside any map.
// The nearest-center search is unrolled (CPRB_SEARCH in cprb_cells.fxh, from tools/build_shaders.py):
// this fxc cannot loop over a constant array without testing every element.

#include "cprb_cells.fxh"

// --- look, tune freely, then rebuild the shaders (tools/build_shaders.py) ----------------------
#define CPRB_FADE_NEAR   45.0f   // camera distance below which there is no fill (pure physical map)
#define CPRB_FADE_FAR    110.0f  // camera distance with full fill
#define CPRB_BORDER_PX   1.6f    // country border half-width in screen pixels
#define CPRB_BORDER_MIN  0.05f   // ...but never thinner than this many map units
#define CPRB_BORDER_ALPHA 0.85f
#define CPRB_BORDER_COLOR float3(0.07f, 0.06f, 0.05f)
#define CPRB_GLOW_PX     12.0f   // width of the country-coloured glow inside each border, screen pixels
#define CPRB_GLOW_MIN    0.7f    // ...but never narrower than this many map units (zoomed in)
#define CPRB_GLOW_ALPHA  0.55f   // at the default opacity; scales with the opacity setting
#define CPRB_PROVINCE_ALPHA 0.30f // faint lines between provinces of the same owner (zoomed out)
#define CPRB_DEFAULT_OPACITY 0.6f // opacity the glow and province line strengths are tuned for
#define CPRB_WARP        0.9f    // how wavy the borders are, in map units (0 = straight lines)

float cprbunit = 0.0f;
float cprbopacity = 0.0f;

// one center: C = position in search units ((coordinate + 2048) / 4096), PACKED = xpack as received
#define CPRB_TEST(C, PACKED) \
	{ \
		float2 v = p - (C); \
		float4 cand = float4(dot(v, v), PACKED, (C)); \
		float4 second = (cand.x < near2.x) ? cand : near2; \
		near2 = (cand.x < near1.x) ? near1 : second; \
		near1 = (cand.x < near1.x) ? cand : near1; \
	}

// two centers sent in one parameter
#define CPRB_PAIR(P) \
	{ \
		float4 e = (P) * inv_scale; \
		float2 fx = frac(e.xz); \
		CPRB_TEST(float2(fx.x, e.y), (P).x) \
		CPRB_TEST(float2(fx.y, e.w), (P).z) \
	}

// colour of a faction as sent by the module, 0..1. one = 1 from the vertex shader (see CPRB_PALETTE).
float3 cprb_owner_color(float owner, float one)
{
	float4 group;
	CPRB_PALETTE(group, owner, one)
	float slot = owner - 4.0f * floor(owner * 0.25f);
	float packed = dot(group, float4(abs(float4(0.0f, 1.0f, 2.0f, 3.0f) - slot) < 0.5f));
	packed = floor(packed / cprbunit + 0.5f);
	float red = floor(packed / 16384.0f);
	float green = floor(packed / 128.0f) - red * 128.0f;
	float blue = packed - floor(packed / 128.0f) * 128.0f;
	float3 c = float3(red, green, blue) / 127.0f;
	// a faction without a colour would come out black: show it grey instead
	return (dot(c, 1.0f) < 0.01f) ? float3(0.55f, 0.55f, 0.55f) : c;
}

// cprb_in = (world x, world y, camera distance, 1); col is the final terrain colour.
float3 cprb_apply(float3 col, float4 cprb_in)
{
	// size of one screen pixel in map units (taken before any dynamic branch)
	float pixel = max(fwidth(cprb_in.x), fwidth(cprb_in.y));
	if (cprbunit <= 0.0f)
		return col;

	float2 p = cprb_in.xy;
	p += CPRB_WARP * float2(sin(p.y * 0.37f + 1.7f * sin(p.x * 0.23f)) + 0.4f * sin(p.y * 1.3f + p.x * 0.7f),
	                        sin(p.x * 0.41f + 1.3f * sin(p.y * 0.29f)) + 0.4f * sin(p.x * 1.1f - p.y * 0.9f));

	// search units: (coordinate + 2048) / 4096, so a received xpack / (16384 * cprbunit) has the
	// owner in its integer part and the x coordinate in its fraction
	// (cprb_in.w is 1: going through it stops the effect compiler from precomputing every
	// cprbpK * inv_scale into an extra constant register, which would not fit)
	float inv_scale = cprb_in.w / (16384.0f * cprbunit);
	p = (p + 2048.0f) / 4096.0f;
	// nearest (near1) and second nearest (near2) center: (squared distance, xpack, x, y)
	float4 near1 = float4(1e30f, 0, 0, 0), near2 = float4(1e30f, 0, 0, 0);
	CPRB_SEARCH
	float owner1 = floor(near1.y * inv_scale);
	float owner2 = floor(near2.y * inv_scale);
	// distance from p to the boundary between the two nearest cells, back in map units
	float edge = 4096.0f * (near2.x - near1.x) / (2.0f * length(near1.zw - near2.zw) + 1e-9f);

	// 0 when zoomed in (physical map), 1 when zoomed out (political map)
	float zoom = smoothstep(CPRB_FADE_NEAR, CPRB_FADE_FAR, cprb_in.z);
	float3 fac = cprb_owner_color(owner1, cprb_in.w);
	// fill strength chosen in game (0..1); the glow and province lines follow it, the border line does not
	float opacity = saturate(cprbopacity / (100.0f * cprbunit));
	float accents = saturate(opacity / CPRB_DEFAULT_OPACITY);

	// fill: country colour shaded by the terrain brightness, so relief stays readable
	col = saturate(col);
	float lum = dot(col, float3(0.30f, 0.59f, 0.11f));
	float3 political = fac * (0.55f + 0.65f * lum);
	col = lerp(col, political, opacity * zoom);

	float aa = pixel + 1e-4f;
	if (abs(owner1 - owner2) > 0.5f)
	{
		// country border: a glow of this side's colour fading inwards, then a fixed dark line on top
		float glow_width = max(CPRB_GLOW_PX * pixel, CPRB_GLOW_MIN);
		float glow = 1.0f - saturate(edge / glow_width);
		col = lerp(col, fac, glow * glow * CPRB_GLOW_ALPHA * accents);

		float width = max(CPRB_BORDER_PX * pixel, CPRB_BORDER_MIN);
		float stroke = 1.0f - smoothstep(width, width + aa, edge);
		col = lerp(col, CPRB_BORDER_COLOR, stroke * CPRB_BORDER_ALPHA);
	}
	else
	{
		float width = max(CPRB_BORDER_PX * pixel, CPRB_BORDER_MIN) * 0.4f;
		float stroke = 1.0f - smoothstep(width, width + aa, edge);
		col = lerp(col, fac * 0.45f, stroke * CPRB_PROVINCE_ALPHA * zoom * accents);
	}
	return col;
}

// The map terrain techniques need shader model 3 for the search above.
#define DEFINE_TECHNIQUES_CPRB(tech_name, vs_name, ps_name)	\
				technique tech_name	\
				{ pass P0 { VertexShader = compile vs_3_0 vs_name(PCF_NONE); \
							PixelShader = compile ps_3_0 ps_name(PCF_NONE);} } \
				technique tech_name##_SHDW	\
				{ pass P0 { VertexShader = compile vs_3_0 vs_name(PCF_DEFAULT); \
							PixelShader = compile ps_3_0 ps_name(PCF_DEFAULT);} } \
				technique tech_name##_SHDWNVIDIA	\
				{ pass P0 { VertexShader = compile vs_3_0 vs_name(PCF_NVIDIA); \
							PixelShader = compile ps_3_0 ps_name(PCF_NVIDIA);} }
