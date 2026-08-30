


typedef unsigned char u8;
typedef short s16;
typedef long s32;
typedef void (*CallbackTaskCallback)(void);
typedef void (*RenderCallback)(void);
extern struct RacePlayer
{
  char pad00[8];
  u8 menuState;
} gRacePlayers[8];
extern void createCallbackTask(CallbackTaskCallback, void *, u8);
extern void removeCallbackTask(void *);
extern void addRenderCallback(void *, RenderCallback, void *);
extern void initRaceSplitscreenSelectOption0Frame(void);
extern void initRaceSplitscreenSelectOption1Frame(void);
extern void initRaceSplitscreenSelectOption2Frame(void);
extern void initRaceSplitscreenSelectOption3Frame(void);
extern void initRaceSplitscreenSelectOption4Frame(void);
extern void drawRaceSplitscreenSelectPlayerCountIcons(void);
extern void *gMenuRenderCallbackList;
struct RaceSplitscreenSelectRowActor
{
    char rpad00[0x18];
    s16 iconX[5];
    char rpad22[0x2];
    u8 state;
    u8 spawnTimer;
    u8 playerCount;
};
void updateRaceSplitscreenSelectPlayerCountIcons(struct RaceSplitscreenSelectRowActor *row)
{
  struct RaceSplitscreenSelectRowActor *new_var;
  s32 i;
  s32 moved;
  u8 stateByte;
  u8 localState;
  stateByte = (localState = row->state);
  new_var = row;
  switch (stateByte)
  {
    case 0:
      moved = 0;
      for (i = 0; i < ((s32) new_var->playerCount); i++)
    {
      if (new_var->iconX[i] < (-0x7C))
      {
        new_var->iconX[i] += 0x10;
        moved++;
        if (row->iconX[i] >= (-0x7C))
        {
          row->iconX[i] = -0x7C;
        }
      }
    }

      new_var->spawnTimer++;
      if (!(row->spawnTimer & 1))
    {
      if (new_var->playerCount < 5)
      {
        new_var->playerCount++;
      }
    }
      if (moved == 0)
    {
      row->state = 1;
      createCallbackTask(initRaceSplitscreenSelectOption0Frame, 0, 0x5F);
      createCallbackTask(initRaceSplitscreenSelectOption1Frame, 0, 0x60);
      createCallbackTask(initRaceSplitscreenSelectOption2Frame, 0, 0x61);
      createCallbackTask(initRaceSplitscreenSelectOption3Frame, 0, 0x62);
      createCallbackTask(initRaceSplitscreenSelectOption4Frame, 0, 0x63);
    }
      localState = new_var->state;
      break;

    case 1:
      if (gRacePlayers[0].menuState == 1)
    {
      row->state = 2;
      localState = row->state;
    }
      break;

    case 2:
      for (i = 0; i < 5; i++)
    {
      row->iconX[i] -= 0x20;
    }

      if (row->iconX[0] < (-0x103))
    {
      row->state = 3;
    }
      localState = row->state;
      break;

    case 3:
      break;

  }

  localState = new_var->state;
  if (localState == 3)
  {
    removeCallbackTask(new_var);
    return;
  }
  addRenderCallback(&gMenuRenderCallbackList, drawRaceSplitscreenSelectPlayerCountIcons, row);
}
