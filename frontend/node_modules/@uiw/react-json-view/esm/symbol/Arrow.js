import { useSymbolsStore } from "../store/Symbols.js";
import { useSymbolsRender } from "../utils/useRender.js";
export var Arrow = props => {
  var _useSymbolsStore = useSymbolsStore(),
    _useSymbolsStore$Arro = _useSymbolsStore.Arrow,
    Comp = _useSymbolsStore$Arro === void 0 ? {} : _useSymbolsStore$Arro;
  useSymbolsRender(Comp, props, 'Arrow');
  return null;
};
Arrow.displayName = 'JVR.Arrow';