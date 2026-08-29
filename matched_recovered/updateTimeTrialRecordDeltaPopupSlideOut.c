


typedef unsigned char u8;
typedef long s32;
struct RaceUiTimeTrialRecordDeltaPopupActor;
extern void removeCallbackTask(struct RaceUiTimeTrialRecordDeltaPopupActor *actor);
extern void addRenderCallback(void **list, void (*cb)(void *), void *arg);
extern void drawTimeTrialRecordDeltaPopup(void *arg);
extern struct RaceUiTimeTrialRecordDeltaPopupActor *gRaceOverlayRenderCallbackList;
typedef struct RaceUiTimeTrialRecordDeltaPopupActor
{
  u8 _pad0[0x1C];
  s32 field1C;
  u8 _pad1[0x8];
  s32 field28;
} RaceUiTimeTrialRecordDeltaPopupActor;
void updateTimeTrialRecordDeltaPopupSlideOut(RaceUiTimeTrialRecordDeltaPopupActor *actor)
{
  s32 v0 = actor->field28;
  void (*new_var)(void *arg);
  s32 t8 = ((((((((v0 + 4) & 0xFFFFFFFFFFFFFFFF) & 0xFFFFFFFFFFFFFFFF) & 0xFFFFFFFFFFFFFFFF) & 0xFFFFFFFFFFFFFFFF) & 0xFFFFFFFFFFFFFFFF) & 0xFFFFFFFFFFFFFFFF) & 0xFFFFFFFFFFFFFFFF) & 0xFFFFFFFFFFFFFFFF;
  actor->field1C -= v0;
  actor->field28 = t8;
  if (t8 == 0x38)
  {
    removeCallbackTask(actor);
  }
  else
  {
    new_var = drawTimeTrialRecordDeltaPopup;
    addRenderCallback(&gRaceOverlayRenderCallbackList, new_var, actor);
  }
}
