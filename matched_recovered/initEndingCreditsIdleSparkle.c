


typedef unsigned char u8;
typedef short s16;
typedef struct EndingCreditsEffectActor EndingCreditsEffectActor;
typedef void (*CallbackFunc)(void *);
extern void setCallbackTaskCallback(EndingCreditsEffectActor *actor, CallbackFunc cb);
extern void updateEndingCreditsIdleSparkle(void *arg);
struct EndingCreditsEffectActor
{
  u8 pad0[0x18];
  s16 linePositions[4];
};
void initEndingCreditsIdleSparkle(EndingCreditsEffectActor *actor)
{
  int new_var;
  actor->linePositions[0] = -0x14;
  new_var = -0x59;
  actor->linePositions[1] = new_var;
 actor->linePositions[2] = 0; actor->linePositions[3] = 0;
  setCallbackTaskCallback(actor, updateEndingCreditsIdleSparkle);
}
