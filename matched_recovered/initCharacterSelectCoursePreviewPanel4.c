


typedef unsigned char u8;
typedef signed char s8;
typedef short s16;
typedef struct 
{
  u8 pad0[24];
  s16 x;
  s16 y;
  s16 spriteIndex;
  s8 flag;
  s8 state;
} CharacterSelectCourseWidgetActor;
extern void setCallbackTaskCallback(CharacterSelectCourseWidgetActor *obj, void (*callback)(void *));
extern void updateCharacterSelectCoursePreviewPanel4(void *obj);
void initCharacterSelectCoursePreviewPanel4(CharacterSelectCourseWidgetActor *obj)
{
  obj->x = -8;
  obj->y = -0x140;
  obj->spriteIndex = 4;
  obj->state = 0;
  obj->flag = 0;
  setCallbackTaskCallback(obj, (void (*)(void *)) updateCharacterSelectCoursePreviewPanel4);
}
