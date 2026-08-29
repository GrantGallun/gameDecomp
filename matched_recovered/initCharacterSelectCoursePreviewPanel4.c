


typedef unsigned char u8;
typedef short s16;
extern void setCallbackTaskCallback(void *obj, void (*callback)(void *));
extern void updateCharacterSelectCoursePreviewPanel4(void *arg);
typedef struct 
{
  u8 pad0[0x18];
  s16 field18;
  s16 field1a;
  s16 field1c;
  u8 field1e;
  u8 field1f;
} CharacterSelectCourseWidgetActor;
void initCharacterSelectCoursePreviewPanel4(CharacterSelectCourseWidgetActor *arg0)
{
  arg0->field18 = -8;
  arg0->field1a = -((0, 0x140));
  arg0->field1c = 4;
  arg0->field1f = (arg0->field1e = 0);
  setCallbackTaskCallback(arg0, updateCharacterSelectCoursePreviewPanel4);
}
