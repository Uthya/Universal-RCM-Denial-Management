import { useSymbolsStore } from "../store/Symbols.js";
import { useSymbolsRender } from "../utils/useRender.js";
export var Colon = props => {
  var _useSymbolsStore = useSymbolsStore(),
    _useSymbolsStore$Colo = _useSymbolsStore.Colon,
    Comp = _useSymbolsStore$Colo === void 0 ? {} : _useSymbolsStore$Colo;
  useSymbolsRender(Comp, props, 'Colon');
  return null;
};
Colon.displayName = 'JVR.Colon';