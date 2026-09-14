#define NULL 0


typedef unsigned long u32;
typedef long s32;
typedef struct RenderCallbackNode
{
  struct RenderCallbackNode *next;
  void (*callback)(s32);
  s32 arg;
} RenderCallbackNode;
typedef struct 
{
  RenderCallbackNode entry0;
  RenderCallbackNode entry1;
  RenderCallbackNode entry2;
  RenderCallbackNode entry3;
} CallbackQueueGroup;
extern RenderCallbackNode *gMenuForegroundRenderCallbackList;
extern RenderCallbackNode *gRaceForegroundRenderCallbackList;
extern RenderCallbackNode *gModelRenderCallbackList;
extern RenderCallbackNode *gBackdropRenderCallbackList;
extern RenderCallbackNode *gMenuOverlayRenderCallbackList;
extern RenderCallbackNode *D_80124848;
extern RenderCallbackNode *gRaceOverlayRenderCallbackList;
extern RenderCallbackNode *gMenuRenderCallbackList;
void resetRenderCallbackQueues(void)
{
  u32 end;
  int new_var;
  CallbackQueueGroup *group;
  gMenuForegroundRenderCallbackList = NULL;
  gRaceForegroundRenderCallbackList = NULL;
 end = (u32) (&gBackdropRenderCallbackList); group = (CallbackQueueGroup *) (&gModelRenderCallbackList); loop: group++; group[-1].entry0.next = NULL;
  group[-1].entry1.next = NULL;
  new_var = -1;
  group[new_var].entry2.next = NULL;
  group[-1].entry3.next = NULL;
  if (((u32) group) != end)
  {
    goto loop;
  }
  gBackdropRenderCallbackList = NULL;
  gMenuOverlayRenderCallbackList = NULL;
  D_80124848 = NULL;
  gRaceOverlayRenderCallbackList = NULL;
  gMenuRenderCallbackList = NULL;
}

