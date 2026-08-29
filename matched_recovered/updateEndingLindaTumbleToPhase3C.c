


typedef unsigned char u8;
typedef unsigned short u16;
typedef long s32;
int stepMainMenuSceneModelAnimation(int model);
int setMainMenuSceneModelPosition(int model, s32 posX, s32 posY, s32 posZ);
void setCallbackTaskCallback(void *model, void (*callback)(void *));
int addMainMenuSceneModelDrawCallback(int model);
int setMainMenuSceneModelAnimation(int model, int anim);
void updateEndingLindaSpinUntilPhase3F(void *model);
typedef struct 
{
  u8 _pad0[0x18];
  s32 posX;
  s32 posY;
  s32 posZ;
  u8 _pad1[6];
  u16 timer;
} EndingCreditsLinda;
void updateEndingLindaTumbleToPhase3C(EndingCreditsLinda *arg0)
{
  int new_var;
  s32 sp20;
  s32 var_v0;
  sp20 = stepMainMenuSceneModelAnimation(3);
  if (arg0->timer < 5)
  {
    var_v0 = 1;
  }
  else
  {
    var_v0 = -1;
  }
  arg0->posX += 0x76000;
  arg0->posY += var_v0 << 19;
  arg0->posZ += (s32) 0xFFFA0000;
  arg0->timer = arg0->timer + 1;
  setMainMenuSceneModelPosition(new_var = 3, arg0->posX, arg0->posY, arg0->posZ);
  addMainMenuSceneModelDrawCallback(new_var);
  if (sp20 == 1)
  {
    arg0->timer = 0;
    setCallbackTaskCallback(arg0, (void (*)(void *)) updateEndingLindaSpinUntilPhase3F);
    setMainMenuSceneModelAnimation(new_var, 0x5D);
  }
}
