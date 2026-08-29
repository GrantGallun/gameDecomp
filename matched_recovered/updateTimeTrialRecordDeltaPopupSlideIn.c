


typedef unsigned short u16;
typedef long s32;
extern void setCallbackTaskCallback(void *task, void (*callback)(void *));
extern void addRenderCallback(void **list, void (*callback)(void *), void *arg);
extern void *gRaceOverlayRenderCallbackList;
extern void updateTimeTrialRecordDeltaPopupHold(void *arg);
extern void drawTimeTrialRecordDeltaPopup(void *arg);
typedef struct
{
  char pad00[0x18];
  u16 timer;
  char pad1a[0x02];
  s32 field_1c;
  char pad20[0x08];
  s32 field_28;
} RaceUiTimeTrialRecordDeltaPopupActor;
void updateTimeTrialRecordDeltaPopupSlideIn(RaceUiTimeTrialRecordDeltaPopupActor *actor)
{
  s32 *new_var;
  s32 old_v0 = actor->field_28;
  s32 old_t6;
  actor->field_1c = actor->field_1c - old_v0;
  new_var = &actor->field_28;
  actor->field_28 = (*new_var) - 4;
  if ((*new_var) == 0)
  {
    actor->timer = 0x5A;
    setCallbackTaskCallback(actor, updateTimeTrialRecordDeltaPopupHold);
  }
  addRenderCallback(&gRaceOverlayRenderCallbackList, drawTimeTrialRecordDeltaPopup, actor);
}
