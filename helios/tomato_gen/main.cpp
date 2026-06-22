// Generate a single Helios tomato plant -> OBJ.  Usage: tomato_gen <out.obj> [seed]
#include "CanopyGenerator.h"
#include <string>
using namespace helios;
int main(int argc, char** argv) {
    std::string out = (argc > 1) ? argv[1] : "tomato.obj";
    unsigned int seed = (argc > 2) ? (unsigned int)std::stoul(argv[2]) : 0u;
    Context context;
    context.seedRandomGenerator(seed);
    CanopyGenerator canopy_generator(&context);
    canopy_generator.seedRandomGenerator(seed);
    TomatoParameters params;
    // absolute leaf texture (default is relative + only resolves from the Helios root)
    params.leaf_texture_file = "/home/mjarnold/Helios/plugins/canopygenerator/textures/TomatoLeaf_big.png";
    // one quad per leaf so the post-export Blender step applies the alpha-cutout leaf
    // texture per-leaf with clean UVs (default 4x3 grid shares a single 0-1 map).
    params.leaf_subdivisions = make_int2(1, 1);
    params.leaf_length = 0.30f;                                // bigger leaves -> denser foliage mass
    params.plant_height = 1.4f;                                // taller, more substantial habit
    params.shoot_color = make_RGBcolor(0.24f, 0.40f, 0.15f);   // deeper green stems (less washed-out)
    params.fruit_color = make_RGBcolor(0.86f, 0.10f, 0.08f);   // vivid tomato red
    params.fruit_radius = 0.055f;                              // larger, more visible fruit
    params.fruit_subdivisions = 14;                            // rounder fruit
    params.buildPlant(canopy_generator, make_vec3(0, 0, 0));
    context.writeOBJ(out);
    return 0;
}
