#include <assert.h>
#include <stdio.h>
#include <VSScript4.h>

int main(void)
{
    const VSSCRIPTAPI *script_api = getVSScriptAPI(VSSCRIPT_API_VERSION);
    assert(script_api);
    const VSAPI *api = script_api->getVSAPI(VAPOURSYNTH_API_VERSION);
    assert(api);
    VSScript *script = script_api->createScript(NULL);
    assert(script);
    int ret = script_api->evaluateBuffer(script,
        "import vapoursynth as vs\nvs.core.std.BlankClip(width=64, height=32, length=1).set_output()\n", "smoke.vpy");
    if (ret) fprintf(stderr, "%s\n", script_api->getError(script));
    assert(ret == 0);
    VSNode *node = script_api->getOutputNode(script, 0);
    assert(node);
    char error[512];
    const VSFrame *frame = api->getFrame(0, node, error, sizeof(error));
    if (!frame) fprintf(stderr, "%s\n", error);
    assert(frame && api->getFrameWidth(frame, 0) == 64 && api->getFrameHeight(frame, 0) == 32);
    api->freeFrame(frame);
    api->freeNode(node);
    script_api->freeScript(script);
    puts("PASS embedded VSScript: Python initialization and frame generation");
    return 0;
}
