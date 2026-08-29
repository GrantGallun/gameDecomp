


typedef short s16;
typedef struct 
{
  char pad[0x20];
  s16 field20;
  s16 field22;
} EndingCreditsEffectActor;
void setCallbackTaskCallback(void *task, void (*callback)(void *));
void updateEndingCreditsCharacterLoopingSparkle(void *arg);
void initEndingCreditsCharacterLoopingSparkle(EndingCreditsEffectActor *arg0)
{
 arg0->field20 = 0; arg0->field22 = 0;
  setCallbackTaskCallback(arg0, updateEndingCreditsCharacterLoopingSparkle);
}
