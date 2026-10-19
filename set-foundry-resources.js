// ============================================================================
//  Warframe Foundry - set resources (workaround for the broken resources import)
// ============================================================================
//  The app's resources/importData action filters keys against the module state
//  itself (state.hasOwnProperty(key)) so NO resource values ever import.  This
//  script writes each value through the app's own resources/setResource action
//  (the same path the Resources page uses for manual per-row entry).
//
//  Usage:
//    1. Open https://warframe-foundry.app/ in your browser.
//    2. Navigate to the Resources page (so the resource list is loaded).
//    3. Open DevTools -> Console, paste this whole script, press Enter.
//
//  The values below were generated from data/warframe-foundry.json
//  (regenerate by re-running the foundry export, then re-pasting).
// ============================================================================

(function () {
  const store = window.$nuxt && window.$nuxt.$store;
  if (!store) {
    console.error("[foundry] Vuex store not found (window.$nuxt missing).");
    return;
  }
  const resModule = store.state.resources;
  if (!resModule || !resModule.resourceKeys) {
    console.error("[foundry] resources module not initialised.");
    return;
  }
  if (Object.keys(resModule.resourceKeys).length === 0) {
    console.warn("[foundry] resource list not loaded yet - open the Resources page first, then re-run.");
  }

  const RESOURCES = {
    "1999ResourceCommonA": 2887,
    "1999ResourceCommonB": 1955,
    "1999ResourceRareA": 65,
    "1999ResourceUncommonA": 1375,
    "Alertium": 93,
    "AlloyPlate": 264881,
    "ArachnoidHungerItem": 11,
    "ArgonCrystal": 2,
    "BioComponent": 2,
    "BothCommonFishAPartItem": 54,
    "BothCommonFishBPartItem": 27,
    "BothRareFishAPartItem": 25,
    "CarbidesRailjackItem": 90430,
    "CetusWispItem": 16,
    "ChemComponent": 12,
    "Circuits": 151018,
    "CommonGemBCutAItem": 5,
    "CommonOreAAlloyAItem": 10,
    "CommonOreBAlloyBItem": 17,
    "ConcentratedGas": 2407,
    "ControlModule": 2854,
    "CoolantItem": 285,
    "CorpusFishExaBrainItem": 52,
    "CorpusFishMuonBatteryItem": 21,
    "CorpusFishParralelBiodeItem": 89,
    "CorpusFishRotobladeItem": 86,
    "CorpusFishThermalLaserItem": 41,
    "CorpusFishVenedoCaseItem": 63,
    "CorpusWidgetAItem": 39,
    "CorpusWidgetBItem": 5,
    "Cryotic": 12239,
    "DayCommonFishAPartItem": 35,
    "DayCommonFishBPartItem": 34,
    "DayCommonFishCPartItem": 15,
    "DayUncommonFishBPartItem": 33,
    "DeimosCommonOreAAlloyItem": 15,
    "DeimosEidolonGemACutItem": 1,
    "DeimosEidolonGemBCutItem": 1,
    "DeimosGenericSharedFishPartItem": 738,
    "DeimosInfestedFishBPartItem": 119,
    "DeimosInfestedFishEPartItem": 60,
    "DeimosInfestedFishFPartItem": 8,
    "DeimosInfestedFishGPartItem": 88,
    "DeimosOrokinFishBPartItem": 39,
    "DeimosOrokinFishCPartItem": 3,
    "DeimosRareGemACutItem": 4,
    "DeimosUncommonGemACutItem": 5,
    "DuviriDragonDropItem": 162,
    "DuviriEnemyDropItem": 1244,
    "DuviriFractalItem": 477,
    "DuviriMurmurItemA": 31,
    "DuviriMushroomItem": 182,
    "DuviriPlantItemA": 1192,
    "DuviriPlantItemB": 89,
    "DuviriPlantItemC": 127,
    "DuviriPlantItemD": 241,
    "DuviriPlantItemE": 872,
    "DuviriPlantItemF": 381,
    "DuviriProcessedItem": 2244,
    "DuviriRockItem": 3302,
    "EidolonFishOilItem": 334,
    "EidolonFishScalesItem": 524,
    "EidolonFruitItem": 348,
    "EidolonGemACutAItem": 1,
    "Eidolonium": 17,
    "EnergyComponent": 13,
    "EntratiLabDogTagCommon": 2,
    "EntratiLabDogTagRare": 6,
    "EntratiLabMiscItemA": 102274,
    "EntratiLabMiscItemB": 141,
    "EntratiWispItem": 1,
    "Ferrite": 862959,
    "ForestRodentPartItem": 15,
    "Forma": 97,
    "FungusHeartItem": 275,
    "Gallium": 440,
    "GenericDuviriFishPartAItem": 101,
    "GenericDuviriFishPartCItem": 5,
    "GenericFishScrapItem": 492,
    "GrnFlameSpearPart": 14,
    "GrokdrulItem": 477,
    "InfGorgaricusSeedItem": 2732,
    "InfMapricoFruitItem": 5319,
    "IraditeItem": 260,
    "IsosRailjackItem": 4015,
    "Kuva": 413500,
    "Morphic": 884,
    "Nanospores": 1667855,
    "NeuralSensor": 63,
    "Neurode": 720,
    "NightLegendaryFishAPartItem": 2,
    "NightRareFishBPartItem": 11,
    "NistlebrushItem": 539,
    "NullstonesRailjackItem": 96,
    "OrokinCell": 164,
    "OrokinFishEntroplasmaItem": 7,
    "OrokinFishSaganModuleItem": 45,
    "OxiumAlloy": 2364,
    "Plastids": 69676,
    "PolymerBundle": 116350,
    "PrimeBucks": 710,
    "QuillsDogTag": 427,
    "QuillsUncommonDogTag": 63,
    "RareOreAAlloyAItem": 15,
    "Rubedo": 271936,
    "Salvage": 559327,
    "SentientShardCommonItem": 5,
    "ShrineFragment": 51,
    "SolarisCommonGemACutItem": 5,
    "SolarisCommonGemBCutItem": 5,
    "SolarisCommonOreAAlloyItem": 49,
    "SolarisCommonOreBAlloyItem": 19,
    "SolarisEidolonGemACutItem": 1,
    "SolarisFishDissipatorCoilItem": 74,
    "SolarisFishLatheCoogulantItem": 17,
    "SolarisFishNeuralRelayItem": 26,
    "SolarisRareGemACutItem": 3,
    "SolarisUncommonOreAAlloyItem": 15,
    "SpecterParticleResidue": 32,
    "Tellurium": 4,
    "TitaniumRailjackItem": 66956,
    "UncommonGemACutAItem": 5,
    "UncommonOreAAlloyAItem": 5,
    "VenusCoconutItem": 45,
    "VenusTreeItem": 190,
    "VoidAngelItem": 2,
    "VoidWraithItem": 170,
    "ZarimanDogTagBounty": 36,
    "ZarimanMiscItemA": 80,
    "ZarimanMiscItemB": 24,  };

  const keys = Object.keys(RESOURCES);
  keys.forEach((key) => {
    store.dispatch("resources/setResource", { key: key, value: RESOURCES[key] });
  });

  console.log(`[foundry] Set ${keys.length} resources.`);
  const spot = ["Nanospores", "OrokinCell", "Rubedo", "Kuva"];
  spot.forEach((k) => {
    if (resModule.resourceKeys[k] !== undefined) {
      console.log(`  ${k}: ${resModule.resourceKeys[k].toLocaleString()}`);
    }
  });
})();
